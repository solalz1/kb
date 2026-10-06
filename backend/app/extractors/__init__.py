"""Aiguillage : quel extracteur pour quel item."""

from __future__ import annotations

import logging
import mimetypes
import tempfile
from pathlib import Path

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
        ex = extract_url(item["input_url"])
    elif item.get("input_text"):
        ex = note.extract(item["input_text"])
    else:
        raise ExtractionError("Rien à traiter : ni URL, ni texte, ni fichier")

    shared = (item.get("input_text") or "").strip()
    if item.get("input_url") and shared and urls.only_url(shared) is None and shared not in ex.content:
        ex.content = f"Extrait partagé :\n> {shared}\n\n{ex.content}"
    return ex


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
