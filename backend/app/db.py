"""Accès Postgres (psycopg 3 + pool). Toutes les requêtes passent par ici."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _configure(conn: Connection) -> None:
    conn.execute("set search_path to public, extensions")


def pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                get_settings().database_url,
                min_size=1,
                max_size=10,
                kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
                configure=_configure,
                open=True,
            )
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def conn() -> Iterator[Connection]:
    with pool().connection() as c:
        yield c


def fetchall(sql: str, params: Sequence[Any] | dict | None = None) -> list[dict]:
    with conn() as c:
        return c.execute(sql, params).fetchall()


def fetchone(sql: str, params: Sequence[Any] | dict | None = None) -> dict | None:
    with conn() as c:
        return c.execute(sql, params).fetchone()


def execute(sql: str, params: Sequence[Any] | dict | None = None) -> None:
    with conn() as c:
        c.execute(sql, params)


def vec(values: Sequence[float]) -> str:
    """Littéral pgvector, à caster en ::vector dans la requête."""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)


def loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value
