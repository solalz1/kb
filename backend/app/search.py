"""Recherche hybride au niveau des chunks, puis regroupement par item."""

from __future__ import annotations

from collections import defaultdict

from . import db, embeddings

ITEM_FIELDS = """id::text, kind, title, source_url, author, author_url, site_name, published_at, created_at,
                 summary, key_points, tags, entities, use_cases, user_note, thumbnail_url, metadata, genre, file_path,
                 space, category, pinned"""


def search_chunks(
    query: str,
    *,
    limit: int = 30,
    kinds: list[str] | None = None,
    tags: list[str] | None = None,
    spaces: list[str] | None = None,
    include_archived: bool = False,
) -> list[dict]:
    vector = embeddings.embed_query(query)
    return db.fetchall(
        """select * from hybrid_search(%s, %s::vector, %s, %s, %s, %s, filter_spaces => %s)""",
        (query, db.vec(vector), limit, kinds or None, tags or None, include_archived, spaces or None),
    )


def search_items(
    query: str,
    *,
    limit: int = 10,
    kinds: list[str] | None = None,
    tags: list[str] | None = None,
    spaces: list[str] | None = None,
    chunks_per_item: int = 3,
) -> list[dict]:
    """Items les plus pertinents, chacun avec ses meilleurs extraits."""
    hits = search_chunks(query, limit=max(30, limit * 4), kinds=kinds, tags=tags, spaces=spaces)
    grouped: dict[str, list[dict]] = defaultdict(list)
    scores: dict[str, float] = defaultdict(float)
    for h in hits:
        iid = str(h["item_id"])
        grouped[iid].append(h)
        # le meilleur chunk compte plein, les suivants un peu (favorise les items riches en passages pertinents)
        scores[iid] += h["score"] * (1.0 if len(grouped[iid]) == 1 else 0.3)
    ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
    return attach_items(ranked, grouped, scores, chunks_per_item)


def attach_items(ids: list[str], grouped: dict[str, list[dict]], scores: dict[str, float], chunks_per_item: int = 3) -> list[dict]:
    if not ids:
        return []
    rows = db.fetchall(f"select {ITEM_FIELDS} from items where id = any(%s::uuid[])", (ids,))
    by_id = {r["id"]: r for r in rows}
    out = []
    for iid in ids:
        if iid not in by_id:
            continue
        item = dict(by_id[iid])
        excerpts = [h for h in grouped.get(iid, []) if h["chunk_index"] >= 0][:chunks_per_item]
        item["excerpts"] = [h["content"] for h in excerpts]
        item["score"] = round(scores.get(iid, 0.0), 5)
        item["similarity"] = max((h["similarity"] or 0) for h in grouped.get(iid, [{"similarity": 0}]))
        out.append(item)
    return out


def merge_rankings(rankings: list[list[dict]], limit: int, k: int = 30) -> list[dict]:
    """Fusion RRF de plusieurs listes d'items (mode projet : une liste par requête)."""
    score: dict[str, float] = defaultdict(float)
    best: dict[str, dict] = {}
    hits: dict[str, int] = defaultdict(int)
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            score[item["id"]] += 1.0 / (k + rank + 1)
            hits[item["id"]] += 1
            if item["id"] not in best:
                best[item["id"]] = item
            else:
                seen = set(best[item["id"]]["excerpts"])
                best[item["id"]]["excerpts"] += [e for e in item["excerpts"] if e not in seen]
    ranked = sorted(score, key=score.get, reverse=True)[:limit]
    out = []
    for iid in ranked:
        it = best[iid]
        it["score"] = round(score[iid], 5)
        it["matched_queries"] = hits[iid]
        it["excerpts"] = it["excerpts"][:4]
        out.append(it)
    return out
