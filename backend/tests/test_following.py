"""People followed on X join the digest's people: newest follows read in small pages, stopping at a known one."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.digest import following

from .test_e2e import AUTH


class FakeX:
    """The X API for one account: `follows` is newest first, as X returns it; every account returned is billed."""

    def __init__(self):
        self.follows = [{"id": str(100 + i), "username": f"old{i}", "name": f"Ancien {i}"} for i in range(12)]
        self.billed = 0
        self.calls = []
        self.fail = None

    def follow(self, username, name):
        self.follows.insert(0, {"id": str(1000 + len(self.follows)), "username": username, "name": name})

    def __call__(self, path, params=None):
        params = params or {}
        self.calls.append((path, dict(params)))
        if self.fail:
            raise following.FollowError(self.fail)
        if path == "/users/by/username/solal_test":
            self.billed += 1
            return {"data": {"id": "42", "username": "solal_test", "name": "Solal",
                             "public_metrics": {"following_count": len(self.follows)}}}
        if path.startswith("/users/by/username/"):
            return {"errors": [{"detail": "Could not find user"}]}
        assert path == "/users/42/following"
        start = int(params.get("pagination_token") or 0)
        size = params["max_results"]
        page = self.follows[start:start + size]
        self.billed += len(page)
        meta = {"result_count": len(page)}
        if start + size < len(self.follows):
            meta["next_token"] = f"{start + size:016d}"
        return {"data": page, "meta": meta}


@pytest.fixture
def x(clean_db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "x_bearer_token", "x-test")
    fake = FakeX()
    monkeypatch.setattr(following, "_get", fake)
    return fake


@pytest.fixture
def client(x, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _people():
    from app import db

    return {r["x_handle"]: r for r in db.fetchall("select * from watch where kind = 'person'")}


def test_link_starts_from_now(client, x):
    r = client.put("/api/x-follow", json={"username": "@solal_test"}, headers=AUTH)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["configured"] and state["username"] == "solal_test" and state["following_count"] == 12
    assert state["import_cost"] == 0.12
    assert _people() == {}                       # follows from before are not added…
    assert x.billed == 1 + following.PAGE        # …and linking read one small page only

    bad = client.put("/api/x-follow", json={"username": "personne_ici"}, headers=AUTH)
    assert bad.status_code == 400 and "introuvable" in bad.json()["detail"]
    assert client.get("/api/interests", headers=AUTH).json()["x_follow"]["username"] == "solal_test"


def test_new_follows_are_added_and_reading_stops_at_a_known_one(client, x):
    from app import db

    client.put("/api/x-follow", json={"username": "solal_test"}, headers=AUTH)
    x.billed = 0
    x.follow("karpathy", "Andrej Karpathy")
    x.follow("lilianweng", "Lilian Weng")
    state = client.post("/api/x-follow/sync", headers=AUTH).json()
    assert set(_people()) == {"karpathy", "lilianweng"}
    assert _people()["karpathy"]["origin"] == "x_follow" and _people()["karpathy"]["status"] == "active"
    assert state["last_added"] == ["Andrej Karpathy", "Lilian Weng"] and state["added_total"] == 2
    assert x.billed == following.PAGE            # one page: the third account was already known

    # nothing new: still one small page, nothing added
    x.billed = 0
    assert client.post("/api/x-follow/sync", headers=AUTH).json()["last_added"] == []
    assert x.billed == following.PAGE

    # more new follows than a page holds: reads on until a known one
    for i in range(7):
        x.follow(f"new{i}", f"Nouveau {i}")
    client.post("/api/x-follow/sync", headers=AUTH)
    assert {f"new{i}" for i in range(7)} <= set(_people())
    assert len(_people()) == 9

    # someone removed in the app (muted) and followed again on X stays removed
    db.execute("update watch set status = 'muted' where x_handle = 'karpathy'")
    x.follows.insert(0, dict(x.follows.pop(next(i for i, u in enumerate(x.follows) if u["username"] == "karpathy")),
                             id="9999"))
    client.post("/api/x-follow/sync", headers=AUTH)
    assert _people()["karpathy"]["status"] == "muted"


def test_import_everything_followed_before(client, x):
    client.put("/api/x-follow", json={"username": "solal_test"}, headers=AUTH)
    x.billed = 0
    state = client.post("/api/x-follow/import", headers=AUTH).json()
    assert len(_people()) == 12 and state["imported_at"]
    assert x.billed == 12                        # the cost shown: one read per account
    assert all(params["max_results"] == following.IMPORT_PAGE for path, params in x.calls[-1:])


def test_daily_sync_before_the_digest(client, x):
    client.put("/api/x-follow", json={"username": "solal_test"}, headers=AUTH)
    x.follow("simonw", "Simon Willison")
    now = datetime.now(timezone.utc)
    assert following.sync_due(now) is None                       # linked just now: not due yet
    assert following.sync_due(now + timedelta(hours=21))["last_added"] == ["Simon Willison"]
    assert following.sync_due(now + timedelta(hours=22)) is None  # synced an hour ago

    # an X error is kept for the Interests page, and never stops the digest
    x.fail = "Crédits X API épuisés (402) : recharge ton compte développeur"
    assert following.sync_due(now + timedelta(days=2)) is None
    assert "402" in client.get("/api/interests", headers=AUTH).json()["x_follow"]["last_error"]
    assert client.post("/api/x-follow/sync", headers=AUTH).status_code == 400


def test_unlink_and_without_x_token(client, x, monkeypatch):
    from app.config import get_settings

    client.put("/api/x-follow", json={"username": "solal_test"}, headers=AUTH)
    assert client.put("/api/x-follow", json={"username": ""}, headers=AUTH).json()["configured"] is False
    assert following.sync_due() is None
    assert client.post("/api/x-follow/sync", headers=AUTH).status_code == 400

    monkeypatch.setattr(get_settings(), "x_bearer_token", "")
    assert client.get("/api/interests", headers=AUTH).json()["x_follow"]["available"] is False
