"""PDF : texte par page (pypdfium2), transcription par Claude pour les scans, vignette de la 1re page."""

from __future__ import annotations

import io
import logging
import re
import threading

import pypdfium2 as pdfium

from .. import llm
from .base import ExtractionError, Extracted, parse_date

log = logging.getLogger(__name__)

SCANNED_CHARS_PER_PAGE = 80
MAX_OCR_PAGES = 60
OCR_BATCH = 10

# PDFium n'est pas thread-safe : un seul thread à la fois dans la bibliothèque.
_PDFIUM_LOCK = threading.Lock()


def _pdf_date(value: str | None):
    if not value:
        return None
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", value)
    return parse_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}") if m else parse_date(value)


def _useful_title(title: str | None) -> str | None:
    if not title:
        return None
    t = title.strip()
    bad = (t.lower().endswith((".doc", ".docx", ".pdf", ".tex", ".dvi")) or t.lower().startswith(("microsoft", "untitled"))
           or len(t) < 4)
    return None if bad else t


def _read(data: bytes) -> tuple[list[str], int, dict, bytes | None, list[tuple[int, bytes]]]:
    """Tout le travail PDFium, sous verrou : texte, métadonnées, vignette et lots de pages pour l'OCR."""
    with _PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise ExtractionError(f"PDF illisible ou protégé par mot de passe : {exc}") from exc
        try:
            n_pages = len(doc)
            pages = []
            for i in range(n_pages):
                page = doc[i]
                text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()
                if text:
                    pages.append(f"[p. {i + 1}]\n{text}")
            meta = doc.get_metadata_dict(skip_empty=True)

            thumb = None
            if n_pages:
                try:
                    buf = io.BytesIO()
                    doc[0].render(scale=0.6).to_pil().save(buf, format="PNG")
                    thumb = buf.getvalue()
                except Exception:
                    log.warning("Vignette PDF impossible", exc_info=True)

            batches: list[tuple[int, bytes]] = []
            if n_pages and sum(len(p) for p in pages) / n_pages < SCANNED_CHARS_PER_PAGE:
                # lots de 10 pages : chaque réponse reste complète et chaque requête sous la limite de taille
                for start in range(0, min(n_pages, MAX_OCR_PAGES), OCR_BATCH):
                    part = pdfium.PdfDocument.new()
                    part.import_pages(doc, pages=list(range(start, min(start + OCR_BATCH, n_pages))))
                    out = io.BytesIO()
                    part.save(out)
                    part.close()
                    batches.append((start + 1, out.getvalue()))
            return pages, n_pages, meta, thumb, batches
        finally:
            doc.close()


def extract_bytes(data: bytes, filename: str | None, source_url: str | None = None) -> Extracted:
    pages, n_pages, meta, thumb, batches = _read(data)

    method = "text"
    content = "\n\n".join(pages)
    if batches:
        method = "claude-ocr"
        parts = []
        for first_page, chunk in batches:
            try:
                parts.append(llm.transcribe_pdf(chunk, first_page=first_page))
            except Exception:
                log.exception("OCR Claude impossible (pages %d+)", first_page)
        content = "\n\n".join(parts) or content

    return Extracted(
        kind="pdf",
        title=_useful_title(meta.get("Title")),
        content=content,
        source_url=source_url,
        author=meta.get("Author") or None,
        published_at=_pdf_date(meta.get("CreationDate")),
        thumbnail_bytes=thumb,
        metadata={"pages": n_pages, "filename": filename, "extraction": method,
                  "ocr_truncated": method == "claude-ocr" and n_pages > MAX_OCR_PAGES},
    )
