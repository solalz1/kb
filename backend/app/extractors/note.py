"""Notes libres (texte tapé ou dicté)."""

from __future__ import annotations

from ..urls import find_urls
from .base import Extracted


def extract(text: str) -> Extracted:
    text = (text or "").strip()
    return Extracted(kind="note", content=text, metadata={"urls": find_urls(text)})
