"""Embeddings Voyage AI (ou un fournisseur factice déterministe pour les tests)."""

from __future__ import annotations

import hashlib
import math
import re
import time

import httpx

from . import costs
from .config import get_settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
MAX_BATCH_TEXTS = 64
MAX_BATCH_CHARS = 200_000   # ~50k tokens : bien en dessous des limites par requête


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """input_type : « document » pour l'indexation, « query » pour une recherche."""
    if not texts:
        return []
    s = get_settings()
    if s.embeddings_provider == "fake":
        return [_fake_embedding(t, s.embed_dim) for t in texts]
    if not s.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY manquant")

    out: list[list[float]] = []
    batch: list[str] = []
    size = 0
    for text in texts:
        text = text[:120_000] or " "
        if batch and (len(batch) >= MAX_BATCH_TEXTS or size + len(text) > MAX_BATCH_CHARS):
            out.extend(_voyage(batch, input_type))
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        out.extend(_voyage(batch, input_type))
    return out


def embed_query(text: str) -> list[float]:
    return embed([text], input_type="query")[0]


def _voyage(batch: list[str], input_type: str) -> list[list[float]]:
    s = get_settings()
    payload = {
        "input": batch,
        "model": s.embed_model,
        "input_type": input_type,
        "output_dimension": s.embed_dim,
        "truncation": True,
    }
    for attempt in range(5):
        r = httpx.post(VOYAGE_URL, json=payload, headers={"Authorization": f"Bearer {s.voyage_api_key}"}, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Voyage {r.status_code} : {r.text[:300]}")
        body = r.json()
        tokens = (body.get("usage") or {}).get("total_tokens") or 0
        costs.record("voyage", costs.voyage_cost(s.embed_model, tokens), model=s.embed_model, units={"tokens": tokens},
                     purpose=input_type)
        data = sorted(body["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
    raise RuntimeError("Voyage indisponible après plusieurs tentatives")


def _fake_embedding(text: str, dim: int) -> list[float]:
    """Sac de mots haché : deux textes qui partagent des mots sont proches."""
    vec = [0.0] * dim
    vec[0] = 0.01  # évite le vecteur nul
    for word in re.findall(r"\w+", text.lower()):
        if len(word) < 3:
            continue
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h >> 20) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
