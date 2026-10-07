"""Applies the database schema when the app starts.

The migration files in supabase/migrations/ are idempotent, so running them on every start keeps the database in step
with the code that was just deployed: a new column never has to be added by hand before merging. Turn it off with
AUTO_MIGRATE=false.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .config import get_settings

log = logging.getLogger(__name__)

# /app/supabase in the Docker image (next to the app package), <repo>/supabase when run from a checkout
_HERE = Path(__file__).resolve()
CANDIDATES = [_HERE.parents[1] / "supabase" / "migrations", _HERE.parents[2] / "supabase" / "migrations"]

# Two containers run side by side during a deploy: only one applies the schema at a time.
ADVISORY_LOCK = 4_212_027_301
# Never hold the app's start for long behind another session's lock (Railway gives the health check 60 s).
LOCK_TIMEOUT = "15s"

status = "not run"   # shown by /api/health


def migration_files() -> list[Path]:
    for folder in CANDIDATES:
        files = sorted(folder.glob("*.sql"))
        if files:
            return files
    return []


def run(database_url: str | None = None) -> list[str]:
    """Runs every migration file in name order, in one session, and returns their names."""
    files = migration_files()
    if not files:
        raise RuntimeError("no migration file found (supabase/migrations)")
    url = database_url or get_settings().database_url
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute(f"set lock_timeout = '{LOCK_TIMEOUT}'")
        conn.execute("select pg_advisory_lock(%s)", (ADVISORY_LOCK,))
        try:
            for f in files:
                # no parameters: the whole file goes through the simple query protocol, several statements at once
                conn.execute(f.read_text(encoding="utf-8"))
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
    return [f.name for f in files]


def run_at_startup() -> str:
    """Applies the schema unless AUTO_MIGRATE=false. Never stops the app from starting: the error goes to the logs
    and to /api/health."""
    global status
    if not get_settings().auto_migrate:
        status = "off"
        return status
    try:
        names = run()
        status = "ok"
        log.info("database schema applied: %s", ", ".join(names))
    except Exception as e:  # noqa: BLE001 — a schema problem must not take the whole app down
        status = f"error: {type(e).__name__}: {e}"[:300]
        log.error("could not apply the database schema: %s", e)
    return status
