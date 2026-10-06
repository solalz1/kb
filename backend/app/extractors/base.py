from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)


class ExtractionError(RuntimeError):
    """Erreur définitive (inutile de réessayer)."""


@dataclass
class Extracted:
    kind: str
    content: str = ""
    title: str | None = None
    source_url: str | None = None
    author: str | None = None
    author_url: str | None = None
    site_name: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    thumbnail_url: str | None = None
    thumbnail_bytes: bytes | None = None   # vignette générée (ex. 1re page d'un PDF), stockée par le pipeline
    metadata: dict = field(default_factory=dict)


def http_client(**kwargs) -> httpx.Client:
    headers = {"User-Agent": BROWSER_UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
    headers.update(kwargs.pop("headers", {}))
    return httpx.Client(headers=headers, follow_redirects=True, timeout=kwargs.pop("timeout", 60), **kwargs)


def parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():  # 20240131 (yt-dlp)
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None
