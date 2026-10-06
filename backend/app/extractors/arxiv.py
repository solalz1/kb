"""Articles scientifiques arXiv : métadonnées officielles + texte intégral du PDF."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from . import pdf
from .base import Extracted, http_client, parse_date

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"


def _meta(arxiv_id: str) -> dict:
    try:
        with http_client() as c:
            r = c.get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id})
        entry = ET.fromstring(r.text).find(f"{ATOM}entry")
        if entry is None:
            return {}
        text = lambda tag: " ".join((entry.findtext(f"{ATOM}{tag}") or "").split())  # noqa: E731
        return {
            "title": text("title"),
            "abstract": text("summary"),
            "published": entry.findtext(f"{ATOM}published"),
            "authors": [a.findtext(f"{ATOM}name") for a in entry.findall(f"{ATOM}author")],
        }
    except Exception:
        log.warning("Métadonnées arXiv indisponibles", exc_info=True)
        return {}


def extract(arxiv_id: str, canonical: str) -> Extracted:
    meta = _meta(arxiv_id)
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    body = ""
    pages = None
    try:
        with http_client(timeout=120) as c:
            r = c.get(pdf_url)
        r.raise_for_status()
        ex_pdf = pdf.extract_bytes(r.content, f"{arxiv_id}.pdf", source_url=canonical)
        body, pages = ex_pdf.content, ex_pdf.metadata.get("pages")
    except Exception:
        log.warning("PDF arXiv indisponible", exc_info=True)

    authors = [a for a in meta.get("authors", []) if a]
    author = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "") if authors else None
    content = (f"Abstract : {meta['abstract']}\n\n" if meta.get("abstract") else "") + body
    return Extracted(
        kind="paper",
        title=meta.get("title"),
        content=content,
        source_url=canonical,
        author=author,
        site_name="arXiv",
        published_at=parse_date(meta.get("published")),
        language="en",
        metadata={"arxiv_id": arxiv_id, "authors": authors, "pdf_url": pdf_url, "pages": pages},
    )
