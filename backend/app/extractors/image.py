"""Images et captures d'écran : description + transcription du texte par Claude (vision)."""

from __future__ import annotations

from .. import llm
from .base import Extracted


def extract_bytes(data: bytes, mime: str | None, filename: str | None, source_url: str | None = None) -> Extracted:
    d = llm.describe_image(data, mime)
    parts = [d.get("description", "").strip()]
    if d.get("text_in_image"):
        parts.append("Texte présent dans l'image :\n" + d["text_in_image"].strip())
    detected = {k: d.get(k) for k in ("source_author", "source_platform", "source_url") if d.get(k)}
    return Extracted(
        kind="image",
        title=d.get("title") or filename,
        content="\n\n".join(p for p in parts if p),
        source_url=source_url,
        author=detected.get("source_author"),
        site_name=detected.get("source_platform"),
        thumbnail_url=source_url,
        metadata={"filename": filename, "detected_source": detected} if detected else {"filename": filename},
    )
