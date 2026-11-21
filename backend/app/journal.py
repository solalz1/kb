"""The journal: Perso notes of category 'journal', each on a calendar day.

Shared by the API (the calendar), advice mode (the recent days, read in full) and the MCP server (`get_journal`)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .config import get_settings

WHERE = "space = 'perso' and category = 'journal' and not archived"

RECENT_DAYS = 14          # advice mode reads these last days of the journal in full
RECENT_BUDGET = 12_000    # characters of journal text sent with them, newest first
MAX_RANGE_DAYS = 90       # get_journal: the longest period asked at once
# « Ce jour-là »: the same day 1, 3 and 6 months ago, then every year back to 10 years
ECHO_MONTHS = (1, 3, 6, *(12 * n for n in range(1, 11)))

_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre")


def today() -> date:
    """Today where the user lives (DIGEST_TIMEZONE), not where the server runs."""
    return datetime.now(ZoneInfo(tz())).date()


def day_sql() -> str:
    """The day a note belongs to. Notes filed in the journal before the calendar existed: the day they were written.
    Takes a `%(tz)s` parameter."""
    return "coalesce(entry_date, (created_at at time zone %(tz)s)::date)"


def tz() -> str:
    return get_settings().digest_timezone


def day_of(it: dict) -> date | None:
    """The journal day of an item row (None when it isn't a journal note)."""
    if it.get("space") not in (None, "perso") or it.get("category") != "journal":
        return None
    value = it.get("entry_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    created = it.get("created_at")
    if isinstance(created, datetime):
        return created.astimezone(ZoneInfo(tz())).date() if created.tzinfo else created.date()
    return None


def day_name(day: date) -> str:
    """« jeudi 20 novembre 2026 »."""
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]} {day.year}"


def label(day: date) -> str:
    """« Journal du jeudi 20 novembre 2026 »."""
    return f"Journal du {day_name(day)}"


def entries(start: date, end: date) -> list[dict]:
    """The notes of the days from `start` to `end` (both included), in the order they were written: the user's own
    text, whatever their processing."""
    day = day_sql()
    return db.fetchall(
        f"""select id::text, kind, status, title, coalesce(input_text, summary, title) as text, source_url,
                   created_at, updated_at, {day} as day
            from items where {WHERE} and {day} >= %(start)s and {day} <= %(end)s order by {day}, created_at""",
        {"tz": tz(), "start": start, "end": end},
    )


def recent(today: date, days: int = RECENT_DAYS, budget: int = RECENT_BUDGET) -> list[dict]:
    """The journal of the last `days` days (today included), newest first, as sources: each note in full while the
    budget lasts, then its summary only. Rows carry the search fields, `entry_date` set to their day."""
    from .search import ITEM_FIELDS

    day = day_sql()
    rows = db.fetchall(
        f"""select {ITEM_FIELDS}, {day} as journal_day, coalesce(input_text, content) as text from items
            where {WHERE} and (status = 'ready' or (kind = 'note' and input_text is not null))
              and {day} > %(since)s and {day} <= %(today)s
            order by {day} desc, created_at desc""",
        {"tz": tz(), "since": today - timedelta(days=days), "today": today},
    )
    out, used = [], 0
    for r in rows:
        r["entry_date"] = r.pop("journal_day")
        text = (r.pop("text") or "").strip()
        if used + len(text) > budget and out:
            text = ""          # past the budget: the summary alone
        used += len(text)
        r["excerpts"] = [text[:6000]] if text else []
        out.append(r)
    return out


def months_before(day: date, months: int) -> date:
    """The same day `months` months earlier; a day the month doesn't have (the 31st) is its last day."""
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(y, m + 1, min(day.day, calendar.monthrange(y, m + 1)[1]))


def ago(months: int) -> str:
    """« il y a 3 mois », « il y a 1 an », « il y a 2 ans »."""
    if months % 12:
        return f"il y a {months} mois"
    years = months // 12
    return f"il y a {years} an" + ("s" if years > 1 else "")


def echoes(day: date) -> list[dict]:
    """« Ce jour-là »: what was written on the same day 1, 3, 6 months and 1 to 10 years before `day`, only for the
    dates that have something, the nearest first. Each: {months, date, entries}."""
    targets = {months_before(day, n): n for n in ECHO_MONTHS}
    rows = db.fetchall(
        f"""select id::text, kind, status, title, coalesce(input_text, summary, title) as text, source_url,
                   created_at, updated_at, {day_sql()} as day
            from items where {WHERE} and {day_sql()} = any(%(days)s) order by {day_sql()} desc, created_at""",
        {"tz": tz(), "days": list(targets)},
    )
    found: dict[date, list[dict]] = {}
    for r in rows:
        found.setdefault(r["day"], []).append(r)
    return [{"months": targets[d], "date": d, "entries": found[d]} for d in sorted(found, reverse=True)]
