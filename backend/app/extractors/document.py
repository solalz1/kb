"""Documents bureautiques et texte : Word, PowerPoint, Excel, EPUB, HTML, CSV, Markdown…"""

from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path

from .base import ExtractionError, Extracted

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".sql", ".tex"}


def extract_bytes(data: bytes, filename: str | None, mime: str | None) -> Extracted:
    filename = filename or "document"
    suffix = Path(filename).suffix.lower() or (mimetypes.guess_extension(mime or "") or "")
    stem = Path(filename).stem

    if suffix in TEXT_EXT or (mime or "").startswith("text/"):
        return Extracted(kind="document", title=stem, content=data.decode("utf-8", errors="replace"),
                         metadata={"filename": filename})

    from markitdown import MarkItDown

    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        try:
            result = MarkItDown(enable_plugins=False).convert(f.name)
        except Exception as exc:
            raise ExtractionError(f"Format non pris en charge ({suffix or mime}) : {exc}") from exc
    text = getattr(result, "markdown", None) or getattr(result, "text_content", "") or ""
    return Extracted(kind="document", title=getattr(result, "title", None) or stem, content=text,
                     metadata={"filename": filename, "format": suffix.lstrip(".")})
