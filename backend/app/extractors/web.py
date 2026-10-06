"""Pages web : articles (trafilatura, Jina Reader en secours) et aiguillage par type de contenu."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import trafilatura

from ..config import get_settings
from .base import ExtractionError, Extracted, http_client, parse_date

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 15 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024 * 1024
THIN = 400


def extract(url: str, canonical: str) -> Extracted:
    with http_client(timeout=60) as c:
        try:
            with c.stream("GET", url) as r:
                if r.status_code in (401, 403, 451) or r.status_code == 999:
                    return _jina_or_fail(url, canonical, f"accès refusé ({r.status_code})")
                if r.status_code == 404:
                    raise ExtractionError("Page introuvable (404)")
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                final_url = str(r.url)
                limit = MAX_HTML_BYTES if "html" in ctype or not ctype else MAX_FILE_BYTES
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ExtractionError("Fichier trop volumineux")
                data = bytes(data)
        except ExtractionError:
            raise
        except Exception as exc:
            log.warning("Téléchargement direct impossible (%s), essai via Jina Reader", exc)
            return _jina_or_fail(url, canonical, str(exc))

    filename = Path(urlsplit(final_url).path).name or "fichier"
    if ctype == "application/pdf" or data[:5] == b"%PDF-":
        from . import pdf

        return pdf.extract_bytes(data, filename, source_url=canonical)
    if ctype.startswith("image/"):
        from . import image

        return image.extract_bytes(data, ctype, filename, source_url=canonical)
    if ctype.startswith(("audio/", "video/")):
        from . import av

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (filename or "media")
            path.write_bytes(data)
            ex = av.extract_file(path, filename, ctype, Path(tmp))
        ex.source_url = canonical
        return ex
    if "html" in ctype or not ctype or ctype in ("application/xhtml+xml",):
        return extract_html(data, final_url, canonical)
    if ctype.startswith("text/"):
        return Extracted(kind="article", content=data.decode("utf-8", errors="replace"), source_url=canonical,
                         title=filename, site_name=urlsplit(canonical).netloc)
    from . import document

    ex = document.extract_bytes(data, filename, ctype)
    ex.source_url = canonical
    return ex


def extract_html(html: str | bytes, url: str, canonical: str) -> Extracted:
    """trafilatura accepte aussi des bytes et détecte l'encodage lui-même."""
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_tables=True, include_comments=False)
    text = trafilatura.extract(
        html, url=url, output_format="markdown", include_tables=True, include_comments=False, include_links=False
    ) or ""
    title = getattr(doc, "title", None) if doc else None
    description = getattr(doc, "description", None) if doc else None
    host = urlsplit(canonical).netloc

    if len(text) < THIN:
        jina = _jina(url)
        if jina and len(jina.get("content", "")) > len(text):
            text = jina["content"]
            title = title or jina.get("title")
    if len(text) < THIN and description and description not in text:
        text = (text + "\n\n" + description).strip()

    ex = Extracted(
        kind="article",
        title=title,
        content=text,
        source_url=canonical,
        author=getattr(doc, "author", None) if doc else None,
        site_name=(getattr(doc, "sitename", None) if doc else None) or host,
        published_at=parse_date(getattr(doc, "date", None) if doc else None),
        language=getattr(doc, "language", None) if doc else None,
        thumbnail_url=getattr(doc, "image", None) if doc else None,
        metadata={"description": description} if description else {},
    )
    if len(text) < THIN:
        ex.metadata["thin_content"] = True
        if any(h in host for h in ("linkedin.com", "instagram.com", "facebook.com")):
            ex.metadata["hint"] = "Contenu derrière un login : partage plutôt une capture d'écran."
    return ex


def _jina(url: str) -> dict | None:
    s = get_settings()
    headers = {"Accept": "application/json", "X-Return-Format": "markdown"}
    if s.jina_api_key:
        headers["Authorization"] = f"Bearer {s.jina_api_key}"
    try:
        with http_client(headers=headers, timeout=60) as c:
            r = c.get(f"https://r.jina.ai/{url}")
        if r.status_code >= 400:
            return None
        data = r.json().get("data") or {}
        return {"content": data.get("content") or "", "title": data.get("title")}
    except Exception:
        log.warning("Jina Reader indisponible", exc_info=True)
        return None


def _jina_or_fail(url: str, canonical: str, reason: str) -> Extracted:
    j = _jina(url)
    if j and len(j["content"]) > 100:
        return Extracted(kind="article", title=j.get("title"), content=j["content"], source_url=canonical,
                         site_name=urlsplit(canonical).netloc, metadata={"via": "jina"})
    raise RuntimeError(f"Page inaccessible : {reason}")
