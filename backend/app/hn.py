"""The Hacker News space: every morning, the day's best stories join the KB in the `hn` space.

`run_due` (the worker's `Scheduler`, every minute) runs once a day after HN_HOUR; « Récupérer maintenant » calls `run`.
A run picks the best stories posted in the last 36 hours (Algolia), minus what the KB already has, and queues them
through `pipeline.ingest(space="hn")`; the worker then reads each article and its discussion
(`extractors/hackernews.py`). At most HN_DAILY_COUNT stories a day, whatever the number of runs."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, pipeline, urls
from .config import get_settings
from .extractors import hackernews

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=36)     # a story posted just before yesterday's run still gets its chance
MIN_POINTS = 20
CANDIDATES = 200
SEEN_KEY = "hn_seen"             # {"ids": {story id: local day picked}}: a story deleted from HN never comes back
SEEN_DAYS = 3                    # longer than the window
LAST_RUN_KEY = "hn_last_run"     # {"date": local day of the last daily run}

_lock = threading.Lock()


@dataclass
class Story:
    id: str
    title: str
    url: str | None
    points: int
    comments: int
    author: str | None
    posted_at: datetime
    text: str | None = None      # an Ask HN / Show HN post's own text (HTML)

    @property
    def discussion_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    def meta(self) -> dict:
        """`metadata.hn` of its card."""
        out = {"id": self.id, "title": self.title, "points": self.points, "comments": self.comments,
               "author": self.author, "discussion_url": self.discussion_url, "posted_at": self.posted_at.isoformat()}
        if self.text:
            out["text"] = self.text
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_day(now: datetime) -> str:
    return now.astimezone(ZoneInfo(get_settings().digest_timezone)).date().isoformat()


def _story(hit: dict) -> Story:
    return Story(id=str(hit["objectID"]), title=(hit.get("title") or "").strip(), url=hit.get("url") or None,
                 points=int(hit.get("points") or 0), comments=int(hit.get("num_comments") or 0),
                 author=hit.get("author"), posted_at=datetime.fromtimestamp(int(hit["created_at_i"]), timezone.utc),
                 text=hit.get("story_text") or None)


def _seen() -> dict[str, str]:
    row = db.fetchone("select value from kb_settings where key = %s", (SEEN_KEY,))
    return dict((db.loads(row["value"]) or {}).get("ids") or {}) if row else {}


def _in_kb(story: Story) -> bool:
    """The article (or the discussion, for a text post) is already in the KB, in any space."""
    clause, params = urls.dedupe_filter(urls.classify(story.url or story.discussion_url))
    return bool(db.fetchone(f"select 1 from items where {clause} limit 1", params))


def _pick(now: datetime, count: int, seen: dict[str, str]) -> tuple[list[Story], int]:
    """The `count` best stories not in the KB yet, and how many were passed over because the user has them."""
    if count <= 0:
        return [], 0
    since = int((now - WINDOW).timestamp())
    hits = hackernews.get_json(f"{hackernews.ALGOLIA}/search", {
        "tags": "story", "numericFilters": f"created_at_i>{since},points>{MIN_POINTS}", "hitsPerPage": CANDIDATES,
    }).get("hits") or []
    stories = sorted((_story(h) for h in hits if h.get("title")), key=lambda s: s.points, reverse=True)
    ids = [s.id for s in stories]
    fetched = {r["id"] for r in db.fetchall(
        "select metadata->'hn'->>'id' as id from items where space = 'hn' and metadata->'hn'->>'id' = any(%s)", (ids,))}
    picked, skipped = [], 0
    for story in stories:
        if len(picked) >= count:
            break
        if story.id in fetched or story.id in seen:
            continue
        if _in_kb(story):
            skipped += 1
            continue
        picked.append(story)
    return picked, skipped


def pick(now: datetime, count: int) -> list[Story]:
    return _pick(now, count, _seen())[0]


def _remember(seen: dict[str, str], stories: list[Story], day: str, now: datetime) -> None:
    oldest = (now.astimezone(ZoneInfo(get_settings().digest_timezone)).date() - timedelta(days=SEEN_DAYS)).isoformat()
    ids = {k: v for k, v in seen.items() if v > oldest}
    ids.update({s.id: day for s in stories})
    db.execute("""insert into kb_settings (key, value) values (%s, %s)
                  on conflict (key) do update set value = excluded.value, updated_at = now()""",
               (SEEN_KEY, db.jsonb({"ids": ids})))


def run(now: datetime | None = None) -> dict:
    """One run: {"added", "skipped"} (skipped: stories the user already has), or {"busy": True} while one is going."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        now = now or _now()
        day = _local_day(now)
        seen = _seen()
        room = get_settings().hn_daily_count - sum(1 for d in seen.values() if d == day)
        stories, skipped = _pick(now, room, seen)
        added = 0
        for story in stories:
            res = pipeline.ingest(url=story.url or story.discussion_url, space="hn", hn=story.meta())
            if res.get("duplicate"):
                skipped += 1
            else:
                added += 1
        if stories:
            _remember(seen, stories, day, now)
        if added:
            log.info("Hacker News : %d stories ajoutées", added)
        return {"added": added, "skipped": skipped}
    finally:
        _lock.release()


def run_due(now: datetime | None = None) -> dict | None:
    """Called every minute: today's run once HN_HOUR has come, once a day. A run that fails gives the day back, so
    the next minute tries again."""
    s = get_settings()
    if not s.hn_enabled:
        return None
    now = now or _now()
    if now.astimezone(ZoneInfo(s.digest_timezone)).hour < s.hn_hour:
        return None
    day = _local_day(now)
    row = db.fetchone("select value->>'date' as date from kb_settings where key = %s", (LAST_RUN_KEY,))
    previous = row["date"] if row else None
    if previous and previous >= day:
        return None
    # compare-and-swap: only one container claims the day
    claimed = db.fetchone(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()
           where kb_settings.value->>'date' is not distinct from %s returning key""",
        (LAST_RUN_KEY, db.jsonb({"date": day}), previous))
    if not claimed:
        return None
    try:
        return run(now)
    except Exception:
        if previous:
            db.execute("update kb_settings set value = %s where key = %s and value->>'date' = %s",
                       (db.jsonb({"date": previous}), LAST_RUN_KEY, day))
        else:
            db.execute("delete from kb_settings where key = %s and value->>'date' = %s", (LAST_RUN_KEY, day))
        raise


class Scheduler:
    def __init__(self):
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="kb-hn-scheduler", daemon=True).start()
        s = get_settings()
        log.info("Hacker News : %d stories chaque jour à %d h (%s)", s.hn_daily_count, s.hn_hour, s.digest_timezone)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(30)          # let the API start first
        while not self._stop.is_set():
            try:
                run_due()
            except Exception:
                log.exception("Planificateur Hacker News")
            self._stop.wait(60)
