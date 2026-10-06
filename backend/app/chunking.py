"""Découpage du contenu en morceaux (~350 tokens) pour l'indexation."""

from __future__ import annotations

import re

TARGET = 1600     # caractères par chunk
OVERLAP = 200
MAX_CHUNKS = 400

_SENT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ0-9\[«\"(])")


def _split_long(paragraph: str) -> list[str]:
    if len(paragraph) <= TARGET:
        return [paragraph]
    pieces, buf = [], ""
    for sent in _SENT.split(paragraph):
        if len(sent) > TARGET:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.extend(sent[i:i + TARGET] for i in range(0, len(sent), TARGET))
            continue
        if buf and len(buf) + len(sent) + 1 > TARGET:
            pieces.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []
    paragraphs: list[str] = []
    for p in re.split(r"\n\s*\n|\n(?=\[\d+:\d{2})|\n(?=\[p\. \d+\])", text):
        p = p.strip()
        if p:
            paragraphs.extend(_split_long(p))

    chunks, buf = [], ""
    for p in paragraphs:
        if buf and len(buf) + len(p) + 2 > TARGET:
            chunks.append(buf)
            tail = buf[-OVERLAP:]
            cut = tail.find(" ")
            buf = (tail[cut + 1:] if cut != -1 else tail) + "\n\n" + p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
        if len(chunks) >= MAX_CHUNKS:
            break
    if buf and len(chunks) < MAX_CHUNKS:
        chunks.append(buf)
    return chunks
