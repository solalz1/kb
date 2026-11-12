"""Hacker News: the public APIs (Algolia search and items, Firebase for the comments' order), keyless.

`get_json` is the only network call (tests replace it); calls are spaced a little, HN has no official quota.
A story's card (`extract`) is its article, read by the usual extractors, then the discussion: the first top-level
comments in HN's order. A text post (Ask HN…) is its own text, then the discussion; an article that can't be read
still makes a card, from the title and the discussion (`metadata.hn.article_unreadable`)."""

from __future__ import annotations

import html
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable

import httpx

from ..config import get_settings
from .base import ExtractionError, Extracted

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


MAX_COMMENT = 1500      # characters kept of one comment


def _text(fragment: str | None) -> str:
    """HN's comment HTML (paragraphs as <p>, links, <i>, <pre><code>, entities) as plain text."""
    if not fragment:
        return ""
    t = re.sub(r"<a\s[^>]*href=\"([^\"]*)\"[^>]*>.*?</a>", lambda m: m.group(1), fragment, flags=re.S | re.I)
    t = re.sub(r"</?i>", "*", t, flags=re.I)
    t = re.sub(r"<p>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", t)]
    t = "\n\n".join(p for p in paragraphs if p)
    return t if len(t) <= MAX_COMMENT else t[: MAX_COMMENT - 1].rstrip() + "…"


def discussion(story_id: str, limit: int) -> list[dict]:
    """The first `limit` top-level comments, {"author", "text"}, in HN's order (Firebase's `kids`); deleted, dead or
    empty ones are skipped."""
    story = get_json(f"{FIREBASE}/item/{story_id}.json") or {}
    order = story.get("kids") or []
    if not order or limit <= 0:
        return []
    tree = get_json(f"{ALGOLIA}/items/{story_id}") or {}
    by_id = {int(c["id"]): c for c in tree.get("children") or [] if c.get("id") is not None}
    out = []
    for kid in order:
        c = by_id.get(int(kid))
        text = _text(c.get("text")) if c else ""
        if not c or not c.get("author") or not text:
            continue
        out.append({"author": c["author"], "text": text})
        if len(out) >= limit:
            break
    return out


def discussion_block(meta: dict, comments: list[dict]) -> str:
    lines = ["## Discussion Hacker News",
             f"{meta.get('points') or 0} points · {meta.get('comments') or 0} commentaires · {meta['discussion_url']}"]
    lines += [f"**{c['author']}** : {c['text']}" for c in comments] or ["Pas encore de commentaires."]
    return "\n\n".join(lines)


def _posted(meta: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(meta["posted_at"]) if meta.get("posted_at") else None
    except ValueError:
        return None


def extract(item: dict, read_article: Callable[[dict], Extracted]) -> Extracted:
    """The card of a story of the HN space. An ExtractionError from the article gives the fallback card at once; any
    other error (a site down, a timeout) propagates so the worker tries again, until its last attempt. An error from
    Hacker News itself always propagates."""
    meta = item["metadata"]["hn"]
    block = discussion_block(meta, discussion(meta["id"], get_settings().hn_comments))
    own = {"kind": "article", "title": meta.get("title") or None, "author": meta.get("author"),
           "site_name": "Hacker News", "published_at": _posted(meta)}
    if item.get("input_url") == meta.get("discussion_url"):         # a text post: no article to read
        post = _text(meta.get("text"))
        return Extracted(**own, source_url=meta["discussion_url"],
                         content=f"{post}\n\n{block}" if post else block)
    try:
        ex = read_article(item)
    except Exception as exc:
        # a site that refuses to be read: the card at once; one that's down or times out: the worker tries again,
        # and the last attempt makes the card from the discussion rather than an error
        last = (item.get("attempts") or 0) >= get_settings().max_attempts
        if not isinstance(exc, ExtractionError) and not last:
            raise
        return Extracted(**own, source_url=item.get("source_url") or item.get("input_url"), content=block,
                         metadata={"hn": {**meta, "article_unreadable": True}})
    ex.content = f"{(ex.content or '').rstrip()}\n\n{block}"
    return ex
