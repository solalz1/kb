"""Optional shared sign-in (SSO_URL): the app opens with the sign-in service's session cookie, the token keeps working."""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import sso
from app.config import get_settings

from .test_e2e import AUTH

SSO_URL = "https://auth.example.com"
OWNER = "owner@example.com"
DAY_MS = 24 * 3600 * 1000


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeService:
    """The sign-in service: signs tokens like it does, serves its public keys, renews."""

    def __init__(self):
        self.rotate()
        self.calls = []
        self.down = False
        self.domain = "example.com"

    def rotate(self):
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.kid = _b64(raw[:9])
        self.pub = _b64(raw)

    def token(self, sub=OWNER, iat=None, since=None, kid=None, key=None):
        now = time.time() * 1000 if iat is None else iat
        since = now if since is None else since
        payload = _b64(json.dumps({"v": 1, "kid": kid or self.kid, "sub": sub, "iat": now, "exp": min(now + 30 * DAY_MS, since + 90 * DAY_MS),
                                   "since": since, "amr": "pwd"}).encode())
        sig = (key or self.key).sign(f"v1.{payload}".encode())
        return f"v1.{payload}.{_b64(sig)}"

    def get(self, url):
        self.calls.append(url)
        if self.down:
            raise RuntimeError("connection refused")
        assert url == f"{SSO_URL}/api/keys"
        return {"domain": self.domain, "keys": [{"kid": self.kid, "pub": self.pub}]}

    def post(self, url, token):
        self.calls.append(url)
        assert url == f"{SSO_URL}/api/renew"
        return {"token": self.token(), "maxAgeMs": 30 * DAY_MS}


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(get_settings(), "sso_url", SSO_URL)
    monkeypatch.setattr(get_settings(), "sso_email", OWNER)
    monkeypatch.setattr(sso, "_http_get", fake.get)
    monkeypatch.setattr(sso, "_http_post", fake.post)
    sso.reset_cache()
    yield fake
    sso.reset_cache()


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


WEB = {"x-kb-client": "web"}


def _with(token):
    return {**WEB, "cookie": f"other=1; {sso.COOKIE}={token}"}


def test_off_by_default(client):
    sso.reset_cache()
    assert client.get("/api/health").json()["sso_login"] is None
    assert client.get("/api/stats", headers=_with("v1.a.b")).status_code == 401
    assert client.get("/api/stats", headers=AUTH).status_code == 200


def test_session_cookie_opens_the_app(client, service):
    assert client.get("/api/health").json()["sso_login"] == SSO_URL + "/"
    good = service.token()
    assert client.get("/api/stats", headers=_with(good)).status_code == 200
    assert client.get("/api/stats", headers=AUTH).status_code == 200, "the token (Shortcut, MCP) keeps working"
    # The client header is required with the cookie: another site can't add it without a CORS preflight.
    assert client.get("/api/stats", headers={"cookie": f"{sso.COOKIE}={good}"}).status_code == 401
    # Forged, foreign or stale sessions.
    other_key = Ed25519PrivateKey.generate()
    assert client.get("/api/stats", headers=_with(service.token(key=other_key))).status_code == 401
    head, payload, sig = good.split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=="))
    forged["sub"] = "attacker@example.com"
    assert client.get("/api/stats", headers=_with(f"{head}.{_b64(json.dumps(forged).encode())}.{sig}")).status_code == 401
    old = time.time() * 1000 - 31 * DAY_MS
    assert client.get("/api/stats", headers=_with(service.token(iat=old))).status_code == 401, "expired"
    assert client.get("/api/stats", headers=_with("garbage")).status_code == 401


def test_only_the_owner(client, service, monkeypatch):
    monkeypatch.setattr(get_settings(), "sso_email", "Owner@Example.com")
    assert client.get("/api/stats", headers=_with(service.token())).status_code == 200
    assert client.get("/api/stats", headers=_with(service.token(sub="someone@example.com"))).status_code == 401
    # Without SSO_EMAIL the shared sign-in stays off: another user of the sign-in service gets nothing.
    monkeypatch.setattr(get_settings(), "sso_email", "")
    assert client.get("/api/health").json()["sso_login"] is None
    assert client.get("/api/stats", headers=_with(service.token())).status_code == 401


def test_junk_cookie_next_to_the_real_one(client, service):
    headers = {**WEB, "cookie": f"{sso.COOKIE}=v1.junk.junk; {sso.COOKIE}={service.token()}"}
    assert client.get("/api/stats", headers=headers).status_code == 200


def test_keys_are_cached_and_rotation_is_picked_up(client, service):
    first = service.token()
    client.get("/api/stats", headers=_with(first))
    client.get("/api/stats", headers=_with(first))
    assert service.calls.count(f"{SSO_URL}/api/keys") == 1, "keys fetched once"
    service.rotate()                      # "sign out everywhere" on the service
    fresh = service.token()
    assert client.get("/api/stats", headers=_with(fresh)).status_code == 200, "a new key is fetched at once"
    assert client.get("/api/stats", headers=_with(first)).status_code == 401, "the old sessions are gone"


def test_service_down(client, service):
    service.down = True
    r = client.get("/api/stats", headers=_with(service.token()))
    assert r.status_code == 503, "no keys at all: closed, with a reason"
    service.down = False
    assert client.get("/api/stats", headers=_with(service.token())).status_code == 200
    service.down = True
    sso._cache["at"] = time.time() - 10 * 60      # cache expired, service down: known keys keep working a while
    assert client.get("/api/stats", headers=_with(service.token())).status_code == 200


def test_renewal_once_a_day(client, service):
    r = client.get("/api/stats", headers=_with(service.token()))
    assert "set-cookie" not in r.headers
    r = client.get("/api/stats", headers=_with(service.token(iat=time.time() * 1000 - DAY_MS - 1000)))
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(sso.COOKIE + "=v1.")
    for part in ("Domain=example.com", "HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
        assert part in cookie


def test_logout(client, service):
    assert client.post("/api/logout").status_code == 403
    r = client.post("/api/logout", headers=WEB)
    assert r.json() == {"ok": True, "login": SSO_URL + "/"}
    assert r.headers["set-cookie"].startswith(f"{sso.COOKIE}=;") and "Max-Age=0" in r.headers["set-cookie"]
    assert "Domain=example.com" in r.headers["set-cookie"]


def test_read_token_rules(service):
    keys = [{"kid": service.kid, "pub": service.pub}]
    now = time.time() * 1000
    assert sso.read_token(service.token(), keys, now)["sub"] == OWNER
    assert sso.read_token(service.token(iat=now + 10 * 60 * 1000), keys, now) is None, "issued in the future"
    assert sso.read_token(service.token(since=now - 91 * DAY_MS, iat=now - DAY_MS), keys, now) is None, "90-day ceiling"
    assert sso.read_token("v1." + "a" * 700, keys, now) is None
