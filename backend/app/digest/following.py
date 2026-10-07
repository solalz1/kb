"""The accounts the user follows on X become people the digest follows.

X returns a user's follows newest first, and charges every account it returns (pay-per-use: $0.01 each). So a sync
reads small pages from the top and stops at the first account it has already seen: following three new people costs
one page of five accounts. The ids of the newest follows are kept in kb_settings to know where the new ones stop.

Linking an account starts from now on: what the user followed before is only imported on request (import_all), at a
cost shown first. People removed in the app are never added back, and nothing is removed when the user unfollows on X
(noticing that would mean reading the whole list).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from .. import db
from ..config import get_settings

log = logging.getLogger(__name__)

API = "https://api.x.com/2"
SETTING = "x_following"
PAGE = 5                  # accounts per page of a daily sync
MAX_PAGES = 10            # a daily sync reads at most 50 accounts; more goes through import_all
IMPORT_PAGE = 1000        # the largest page X allows, for import_all
KEEP_IDS = 300            # newest follow ids remembered
SYNC_EVERY = timedelta(hours=20)
COST_PER_ACCOUNT = 0.01   # USD per account returned (X API pay-per-use, "Following/Followers: Read")


class FollowError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    token = get_settings().x_bearer_token
    if not token:
        raise FollowError("X_BEARER_TOKEN manquant")
    r = httpx.get(f"{API}{path}", params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 401:
        raise FollowError("X_BEARER_TOKEN invalide (401)")
    if r.status_code == 402:
        raise FollowError("Crédits X API épuisés (402) : recharge ton compte développeur")
    if r.status_code == 429:
        raise FollowError("Limite de débit X atteinte (429), nouvel essai plus tard")
    if r.status_code >= 400:
        raise FollowError(f"X API {r.status_code} : {r.text[:200]}")
    return r.json()


def state() -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (SETTING,))
    return dict(row["value"]) if row else {}


def _save(value: dict) -> None:
    db.execute(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (SETTING, db.jsonb(value)),
    )


def public_state() -> dict:
    """What the Interests page shows."""
    s = state()
    return {
        "configured": bool(s.get("user_id")),
        "available": bool(get_settings().x_bearer_token),
        "username": s.get("username"),
        "following_count": s.get("following_count"),
        "import_cost": round((s.get("following_count") or 0) * COST_PER_ACCOUNT, 2),
        "imported_at": s.get("imported_at"),
        "last_sync_at": s.get("last_sync_at"),
        "last_added": s.get("last_added") or [],
        "added_total": s.get("added_total") or 0,
        "last_error": s.get("last_error"),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def link(username: str) -> dict:
    """Link the user's X account. Follows from before are not added (see import_all); the newest ones become the
    starting point."""
    username = username.strip().lstrip("@").split("/")[-1]
    if not re.fullmatch(r"\w{1,15}", username):
        raise FollowError("Identifiant X invalide")
    found = _get(f"/users/by/username/{username}", {"user.fields": "public_metrics"})
    user = found.get("data")
    if not user:
        raise FollowError(f"Compte X introuvable : @{username}")
    page = _page(user["id"], PAGE)
    _save({
        "username": user.get("username") or username,
        "user_id": user["id"],
        "following_count": (user.get("public_metrics") or {}).get("following_count"),
        "recent_ids": [u["id"] for u in page.get("data") or []],
        "linked_at": _now(), "last_sync_at": _now(), "last_added": [], "added_total": 0, "last_error": None,
    })
    return public_state()


def unlink() -> dict:
    db.execute("delete from kb_settings where key = %s", (SETTING,))
    return public_state()


def _page(user_id: str, size: int, token: str | None = None) -> dict:
    params = {"max_results": size}
    if token:
        params["pagination_token"] = token
    return _get(f"/users/{user_id}/following", params)


def _add(users: list[dict]) -> list[str]:
    """Adds the accounts nobody follows yet in the app, oldest follow first. An account already there (even
    removed, i.e. muted) is left as it is."""
    added = []
    for u in reversed(users):
        handle = u.get("username")
        if not handle:
            continue
        exists = db.fetchone("select 1 from watch where lower(x_handle) = lower(%s)", (handle,))
        if exists:
            continue
        db.execute(
            """insert into watch (kind, name, x_handle, url, origin, status)
               values ('person', %s, %s, %s, 'x_follow', 'active')""",
            (u.get("name") or handle, handle, f"https://x.com/{handle}"))
        added.append(u.get("name") or handle)
    return added


def sync(now: datetime | None = None) -> dict:
    """Reads the newest follows until one is already known, and adds the new accounts."""
    when = (now or datetime.now(timezone.utc)).isoformat()
    s = state()
    if not s.get("user_id"):
        raise FollowError("Aucun compte X relié")
    seen = set(s.get("recent_ids") or [])
    fresh: list[dict] = []
    token = None
    try:
        for _ in range(MAX_PAGES):
            page = _page(s["user_id"], PAGE, token)
            users = page.get("data") or []
            stop = False
            for u in users:
                if u["id"] in seen:
                    stop = True
                    break
                fresh.append(u)
            token = (page.get("meta") or {}).get("next_token")
            if stop or not token or not seen:
                break
    except FollowError as exc:
        s.update(last_error=str(exc), last_sync_at=when)
        _save(s)
        raise
    added = _add(fresh)
    s.update(
        recent_ids=([u["id"] for u in fresh] + list(s.get("recent_ids") or []))[:KEEP_IDS],
        last_sync_at=when, last_error=None, last_added=added,
        added_total=(s.get("added_total") or 0) + len(added),
        following_count=(s.get("following_count") or 0) + len(fresh) if s.get("following_count") is not None else None,
    )
    _save(s)
    if added:
        log.info("Abonnements X : %d nouvelle(s) personne(s) suivie(s)", len(added))
    return public_state()


def sync_due(now: datetime | None = None) -> dict | None:
    """Called before the daily digest: a sync if the last one is old enough. Never raises."""
    s = state()
    if not s.get("user_id") or not get_settings().x_bearer_token:
        return None
    last = s.get("last_sync_at")
    now = now or datetime.now(timezone.utc)
    if last and now - datetime.fromisoformat(last) < SYNC_EVERY:
        return None
    try:
        return sync(now)
    except Exception:
        log.warning("Synchro des abonnements X échouée", exc_info=True)
        return None


def import_all() -> dict:
    """Adds every account the user follows (one read per account: the cost shown in the app)."""
    s = state()
    if not s.get("user_id"):
        raise FollowError("Aucun compte X relié")
    users: list[dict] = []
    token = None
    while True:
        page = _page(s["user_id"], IMPORT_PAGE, token)
        users += page.get("data") or []
        token = (page.get("meta") or {}).get("next_token")
        if not token:
            break
    added = _add(users)
    s.update(
        recent_ids=[u["id"] for u in users[:KEEP_IDS]], following_count=len(users), imported_at=_now(),
        last_sync_at=_now(), last_error=None, last_added=added, added_total=(s.get("added_total") or 0) + len(added),
    )
    _save(s)
    return public_state()
