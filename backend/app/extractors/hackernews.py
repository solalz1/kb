"""Hacker News: the public APIs (Algolia search and items, Firebase for the comments' order), keyless.

`get_json` is the only network call (tests replace it); calls are spaced a little, HN has no official quota."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
SPACING = 0.2          # seconds between two calls

_spacing = threading.Lock()
_last_call = 0.0


def get_json(url: str, params: dict | None = None) -> Any:
    global _last_call
    with _spacing:
        wait = _last_call + SPACING - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    r = httpx.get(url, params=params, timeout=20, headers={"User-Agent": "kb-hn/1.0"}, follow_redirects=True)
    r.raise_for_status()
    return r.json()
