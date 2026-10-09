"""Aiguillage : quel extracteur pour quel item."""

from __future__ import annotations

import json
import logging
import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .. import storage, urls
from . import arxiv, av, document, github, image, note, pdf, twitter, web, youtube
from .base import ExtractionError, Extracted

log = logging.getLogger(__name__)

__all__ = ["extract_item", "Extracted", "ExtractionError"]


def extract_item(item: dict) -> Extracted:
    if item.get("file_path"):
        data = storage.download(item["file_path"])
        ex = extract_file(data, item.get("file_name"), item.get("file_mime"))
    elif item.get("input_url"):
        ex = _phone_html(item) or _read_url(item)
    elif item.get("input_text"):
        ex = note.extract(item["input_text"])
    else:
        raise ExtractionError("Rien à traiter : ni URL, ni texte, ni fichier")

    shared = (item.get("input_text") or "").strip()
    if item.get("input_url") and shared and urls.only_url(shared) is None and shared not in ex.content:
        ex.content = f"Extrait partagé :\n> {shared}\n\n{ex.content}"
    return ex


def _read_url(item: dict) -> Extracted:
    """The server reads the link; the page's text sent from Safari rescues a site that refuses it."""
    page = _phone_page(item)
    try:
        ex = extract_url(item["input_url"])
    except Exception as exc:
        if not page:
            raise
        # the site blocks the server (or is down for it), but the phone sent the page it shows
        log.info("Le serveur n'a pas pu lire %s (%s) : texte envoyé par le téléphone", item["input_url"], exc)
        return _from_page(item, page)
    return _from_page(item, page, base=ex) if page and _page_is_better(ex, page) else ex


def _meta(item: dict) -> dict:
    meta = item.get("metadata") or {}
    return json.loads(meta) if isinstance(meta, str) else meta


def _phone_html(item: dict) -> Extracted | None:
    """The page the phone fetched for a site that refuses the server (the Shortcut, POST /api/items/<id>/page), when
    it holds an article: read like the server would have."""
    path = _meta(item).get("page_html_path")
    if not path:
        return None
    try:
        html = storage.download(path)
    except Exception:
        log.warning("Page du téléphone introuvable : %s", path, exc_info=True)
        return None
    canonical = urls.classify(item["input_url"]).canonical
    ex = web.extract_html(html, item["input_url"], canonical, jina=False)
    if ex.metadata.get("thin_content") or web._blocked(ex.content):
        log.info("Page du téléphone sans article pour %s : le serveur essaie lui-même", item["input_url"])
        return None
    ex.metadata["via"] = "phone"
    return ex


def _phone_page(item: dict) -> str | None:
    """The page's text as the phone showed it, when it was shared from Safari (pipeline.ingest keeps it aside)."""
    path = _meta(item).get("page_path")
    if not path:
        return None
    try:
        return storage.download(path).decode("utf-8", errors="replace").strip() or None
    except Exception:
        log.warning("Texte de la page introuvable : %s", path, exc_info=True)
        return None


def _page_is_better(ex: Extracted, page: str) -> bool:
    """An article the server only got a sliver of (a login wall, a paywall preview) while the phone had it all."""
    return ex.kind == "article" and (bool(ex.metadata.get("thin_content"))
                                     or (len(ex.content) < 3000 and len(page) > 3 * len(ex.content)))


def _from_page(item: dict, page: str, base: Extracted | None = None) -> Extracted:
    source = (base.source_url if base else None) or urls.classify(item["input_url"]).canonical
    metadata = {k: v for k, v in (base.metadata if base else {}).items() if k not in ("thin_content", "hint")}
    return Extracted(kind="article", title=base.title if base else None, content=page, source_url=source,
                     author=base.author if base else None, site_name=(base.site_name if base else None)
                     or urlsplit(source).netloc, published_at=base.published_at if base else None,
                     language=base.language if base else None, thumbnail_url=base.thumbnail_url if base else None,
                     metadata={**metadata, "via": "phone"})


def extract_url(url: str) -> Extracted:
    info = urls.classify(url)
    if info.kind == "tweet":
        return twitter.extract(info.ids["tweet_id"], info.ids.get("user"))
    if info.kind == "youtube":
        return youtube.extract(info.ids["video_id"], info.canonical)
    if info.kind == "paper":
        return arxiv.extract(info.ids["arxiv_id"], info.canonical)
    if info.kind == "repo":
        try:
            return github.extract(info.ids["owner"], info.ids["repo"], info.canonical)
        except ExtractionError:
            return web.extract(url, info.canonical)
    if info.kind == "media":
        try:
            return av.extract_url(url, info.canonical)
        except Exception as exc:
            # ex. post Instagram sans vidéo : on se rabat sur la page web
            log.info("yt-dlp a échoué (%s), extraction web", exc)
            return web.extract(url, info.canonical)
    return web.extract(url, info.canonical)


def extract_file(data: bytes, filename: str | None, mime: str | None) -> Extracted:
    mime = (mime or mimetypes.guess_type(filename or "")[0] or "application/octet-stream").lower()
    suffix = Path(filename or "").suffix.lower()
    if mime == "application/pdf" or suffix == ".pdf" or data[:5] == b"%PDF-":
        return pdf.extract_bytes(data, filename)
    if mime.startswith("image/") or suffix in (".heic", ".heif", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return image.extract_bytes(data, mime, filename)
    if mime.startswith(("audio/", "video/")) or suffix in (".m4a", ".mp3", ".wav", ".mov", ".mp4", ".aac", ".ogg", ".webm"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (Path(filename or "media").name or "media")
            path.write_bytes(data)
            return av.extract_file(path, filename, mime, Path(tmp))
    if suffix in (".webloc", ".url"):
        found = urls.find_urls(data.decode("utf-8", errors="replace"))
        if found:
            return extract_url(found[0])
    return document.extract_bytes(data, filename, mime)
