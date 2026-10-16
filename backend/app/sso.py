"""Optional shared sign-in: one session for several apps under the same domain.

When ``SSO_URL`` is set (e.g. ``https://auth.example.com``), the web app can also be opened with the session cookie of
that sign-in service instead of ``KB_API_TOKEN``. The service signs its sessions with Ed25519 and publishes the public
keys at ``<SSO_URL>/api/keys``; this module only checks signatures (it can never create a session), renews the cookie
once a day through ``<SSO_URL>/api/renew``, and caches the keys a few minutes. The Shortcut and the MCP connector keep
using the token.

Token: ``v1.<payload, base64url JSON>.<Ed25519 signature of "v1.<payload>", base64url>``; the payload holds ``kid``,
``sub`` (the owner's e-mail), ``iat``, ``exp`` and ``since`` (milliseconds) and ``amr``.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import get_settings

log = logging.getLogger("kb.sso")

COOKIE = "__Secure-sso"
CLIENT_HEADER = "x-kb-client"        # the web app adds it to every call: other sites can't without a CORS preflight
SESSION_MS = 30 * 24 * 3600 * 1000
RENEW_AFTER_MS = 24 * 3600 * 1000
MAX_SESSION_MS = 90 * 24 * 3600 * 1000
CLOCK_SKEW_MS = 5 * 60 * 1000
KEYS_TTL_S = 5 * 60                  # "sign out everywhere" reaches the KB within this delay
KEYS_STALE_MAX_S = 60 * 60           # sign-in service down: known keys keep working this long
MISS_RETRY_S = 30
TIMEOUT_S = 4.0

UNAVAILABLE = "unavailable"

_lock = threading.Lock()
_cache: dict[str, Any] = {"keys": None, "domain": "", "at": 0.0, "miss": 0.0}


def base_url() -> str:
    return get_settings().sso_url.strip().rstrip("/")


def enabled() -> bool:
    """On only with both SSO_URL and SSO_EMAIL: a sign-in service may have other users, and none of them should get
    this knowledge base."""
    return bool(base_url() and get_settings().sso_email.strip())


def login_url(next_url: str = "") -> str:
    return base_url() + "/" + (f"?next={quote(next_url, safe='')}" if next_url else "")


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _http_get(url: str) -> dict:
    return httpx.get(url, timeout=TIMEOUT_S, headers={"accept": "application/json"}).raise_for_status().json()


def _http_post(url: str, token: str) -> dict:
    r = httpx.post(url, timeout=TIMEOUT_S, headers={"authorization": f"Bearer {token}", "accept": "application/json"})
    return r.raise_for_status().json()


def _fetch_keys(now: float) -> dict | None:
    try:
        data = _http_get(base_url() + "/api/keys")
        keys = [k for k in data.get("keys", []) if isinstance(k, dict) and isinstance(k.get("kid"), str) and isinstance(k.get("pub"), str)]
        if not isinstance(data.get("domain"), str) or not data["domain"]:
            raise ValueError("no domain")
        with _lock:
            _cache.update(keys=keys, domain=data["domain"], at=now)
    except Exception as exc:  # noqa: BLE001 — any failure: keep what we know, for a while
        log.warning("sign-in service unreachable: %s", type(exc).__name__)
    with _lock:
        if _cache["keys"] is not None and now - _cache["at"] < KEYS_STALE_MAX_S:
            return {"keys": _cache["keys"], "domain": _cache["domain"]}
    return None


def _keys(now: float) -> dict | None:
    with _lock:
        if _cache["keys"] is not None and now - _cache["at"] < KEYS_TTL_S:
            return {"keys": _cache["keys"], "domain": _cache["domain"]}
    return _fetch_keys(now)


def _split(token: str) -> tuple[str, str] | None:
    if not isinstance(token, str) or len(token) > 600:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _payload(raw: str) -> dict | None:
    try:
        data = json.loads(_b64(raw))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def read_token(token: str, keys: list[dict], now_ms: float) -> dict | None:
    """The payload if the signature is right and the session current, else None (same rules as the service)."""
    parts = _split(token)
    if not parts:
        return None
    payload_raw, sig_raw = parts
    data = _payload(payload_raw)
    if not data or data.get("v") != 1 or not isinstance(data.get("kid"), str) or not isinstance(data.get("sub"), str) or not data["sub"]:
        return None
    if not all(isinstance(data.get(k), (int, float)) and not isinstance(data.get(k), bool) for k in ("iat", "exp", "since")):
        return None
    key = next((k for k in keys if k.get("kid") == data["kid"]), None)
    if not key:
        return None
    try:
        raw = _b64(key["pub"])
        if len(raw) != 32:
            return None
        Ed25519PublicKey.from_public_bytes(raw).verify(_b64(sig_raw), ("v1." + payload_raw).encode())
    except (InvalidSignature, ValueError, TypeError):
        return None
    if data["exp"] <= now_ms or data["iat"] > now_ms + CLOCK_SKEW_MS or data["since"] > data["iat"] or now_ms - data["since"] >= MAX_SESSION_MS:
        return None
    return data


def cookie_values(header: str | None) -> list[str]:
    """Every value sent under the cookie's name: another subdomain could plant a junk one next to the real one."""
    out = []
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            out.append(value)
    return out[:5]


def session_from(header: str | None, now: float | None = None) -> dict | str | None:
    """The first valid session among the cookies of a request (see session())."""
    for value in cookie_values(header):
        found = session(value, now)
        if found:
            return found
    return None


def session(cookie_value: str | None, now: float | None = None) -> dict | str | None:
    """The session for this cookie: a dict (payload, plus the token and the cookie domain), UNAVAILABLE when the keys
    can't be had at all (fail closed), or None."""
    if not enabled() or not cookie_value:
        return None
    now = time.time() if now is None else now
    found = _keys(now)
    if not found:
        return UNAVAILABLE
    data = read_token(cookie_value, found["keys"], now * 1000)
    parts = _split(cookie_value)
    kid = (_payload(parts[0]) or {}).get("kid") if parts else None
    if data is None and kid and not any(k.get("kid") == kid for k in found["keys"]):
        with _lock:
            retry = now - _cache["miss"] > MISS_RETRY_S
            if retry:
                _cache["miss"] = now
        if retry:
            found = _fetch_keys(now) or found
            data = read_token(cookie_value, found["keys"], now * 1000)
    if not data:
        return None
    if data["sub"].strip().lower() != get_settings().sso_email.strip().lower():
        return None
    return {**data, "token": cookie_value, "domain": found["domain"]}


def cookie(token: str, domain: str, max_age_ms: float) -> str:
    dom = f"; Domain={domain}" if domain and domain != "localhost" else ""
    return f"{COOKIE}={token}{dom}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={max(0, int(max_age_ms // 1000))}"


def cleared_cookie() -> str:
    """Expires the shared cookie on this device (on the parent domain, read from the service's keys)."""
    found = _keys(time.time())
    return cookie("", found["domain"] if found else "", 0)


def renewal(sess: dict, now: float | None = None) -> str | None:
    """A fresh Set-Cookie value once a day, so an active session keeps going (up to 90 days). Never raises."""
    now = time.time() if now is None else now
    if not isinstance(sess, dict) or now * 1000 - sess["iat"] < RENEW_AFTER_MS:
        return None
    try:
        out = _http_post(base_url() + "/api/renew", sess["token"])
        if isinstance(out.get("token"), str) and isinstance(out.get("maxAgeMs"), (int, float)):
            return cookie(out["token"], sess["domain"], out["maxAgeMs"])
    except Exception as exc:  # noqa: BLE001
        log.warning("session renewal failed: %s", type(exc).__name__)
    return None


def reset_cache() -> None:
    with _lock:
        _cache.update(keys=None, domain="", at=0.0, miss=0.0)
