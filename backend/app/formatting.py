"""Mise en forme texte des items (contexte du chat, réponses du serveur MCP)."""

from __future__ import annotations

from .config import get_settings
from .taxonomy import CHARTER_CATEGORIES, KIND_LABELS, category_label


def _date(value) -> str | None:
    return str(value)[:10] if value else None


def item_meta_line(it: dict) -> str:
    parts = [KIND_LABELS.get(it.get("kind") or "", it.get("kind") or "élément")]
    if it.get("space") == "perso":
        cat = category_label(it.get("category"))
        perso = "Perso" + (f" · {cat}" if cat else "")
        if it.get("category") in CHARTER_CATEGORIES:
            perso += " (charte)"
        parts.insert(0, perso)
    if it.get("author"):
        parts.append(it["author"])
    if it.get("site_name") and it.get("site_name") not in (it.get("author") or ""):
        parts.append(it["site_name"])
    if d := _date(it.get("published_at")):
        parts.append(f"publié le {d}")
    if d := _date(it.get("created_at")):
        parts.append(f"sauvé le {d}")
    return " · ".join(parts)


def source_block(n: int, it: dict, max_excerpt: int = 1800) -> str:
    lines = [f'<source n="{n}">', f"Titre : {it.get('title') or '(sans titre)'}", f"Infos : {item_meta_line(it)}"]
    if it.get("source_url"):
        lines.append(f"URL : {it['source_url']}")
    if it.get("user_note"):
        lines.append(f"Pourquoi l'utilisateur l'a gardé : {it['user_note']}")
    if it.get("summary"):
        lines.append(f"Résumé : {it['summary']}")
    if it.get("key_points"):
        lines.append("Points clés :\n" + "\n".join(f"- {p}" for p in it["key_points"]))
    if it.get("use_cases"):
        lines.append("Utile pour : " + " ; ".join(it["use_cases"]))
    for ex in it.get("excerpts") or []:
        lines.append("Extrait :\n« " + ex[:max_excerpt].strip() + " »")
    lines.append("</source>")
    return "\n".join(lines)


def source_card(n: int, it: dict) -> dict:
    """Ce que le front affiche pour une source citée [n]."""
    return {
        "n": n,
        "id": it["id"],
        "title": it.get("title"),
        "kind": it.get("kind"),
        "author": it.get("author"),
        "source_url": it.get("source_url"),
        "kb_url": get_settings().item_url(it["id"]),
        "published_at": _date(it.get("published_at")),
        "thumbnail_url": it.get("thumbnail_url"),
        "summary": (it.get("summary") or "")[:400],
        "space": it.get("space") or "main",
        "category": it.get("category"),
        "translations": {code: {k: (v[:400] if k == "summary" else v) for k, v in tr.items() if k in ("title", "summary")}
                         for code, tr in (it.get("translations") or {}).items()},
    }


def item_markdown(it: dict, *, full: bool = False, with_content: bool = False, max_content: int = 30_000) -> str:
    s = get_settings()
    lines = [f"### {it.get('title') or '(sans titre)'}", item_meta_line(it)]
    if it.get("source_url"):
        lines.append(f"Source originale : {it['source_url']}")
    lines.append(f"Fiche KB : {s.item_url(it['id'])}  (id : {it['id']})")
    if it.get("user_note"):
        lines.append(f"Pourquoi je l'ai gardé : {it['user_note']}")
    if it.get("summary"):
        lines.append(f"\n{it['summary']}")
    if full and it.get("key_points"):
        lines.append("\nPoints clés :\n" + "\n".join(f"- {p}" for p in it["key_points"]))
    if it.get("use_cases"):
        lines.append("\nUtile pour :\n" + "\n".join(f"- {u}" for u in it["use_cases"]))
    if it.get("tags"):
        lines.append("\nTags : " + ", ".join(it["tags"]))
    if full and it.get("entities"):
        lines.append("Entités : " + ", ".join(e["name"] for e in it["entities"]))
    for ex in (it.get("excerpts") or [])[:3]:
        lines.append("\n> " + ex[:700].replace("\n", "\n> "))
    if with_content and it.get("content"):
        content = it["content"]
        if len(content) > max_content:
            content = content[:max_content] + "\n[… tronqué]"
        lines.append("\n---\nContenu complet :\n" + content)
    return "\n".join(lines)
