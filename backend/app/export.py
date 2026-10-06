"""Export Markdown : frontmatter YAML + [[liens]] entre éléments.

S'ouvre tel quel dans Obsidian, et s'importe dans Notion (Importer > Texte et Markdown, en envoyant le zip).
Dossiers : KB/Veille/… et KB/Perso/<Catégorie>/… ; avec les fichiers d'origine en option (KB/Fichiers/…).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unicodedata
import zipfile
from pathlib import PurePosixPath

from . import db, storage
from .taxonomy import CATEGORIES, KIND_LABELS, SPACE_LABELS, category_label

log = logging.getLogger(__name__)


def _slug(text: str, max_len: int = 80) -> str:
    """Nom de fichier lisible (accents conservés), sans les caractères interdits par l'OS ou les [[liens]] Obsidian."""
    text = unicodedata.normalize("NFC", text or "sans titre")
    text = re.sub(r'[\\/:*?"<>|#^\[\]\n\r\t]', " ", text)
    return re.sub(r"\s+", " ", text)[:max_len].strip(" .") or "sans titre"


def _yaml(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _folder(it: dict) -> str:
    if it.get("space") == "perso":
        cat = it.get("category")
        return f"KB/Perso/{CATEGORIES[cat][1]}" if cat in CATEGORIES else "KB/Perso/Divers"
    return "KB/Veille"


def _markdown(it: dict, names: dict[str, str], related: list[tuple[str, str]], file_ref: str | None) -> str:
    iid = str(it["id"])
    fm = {
        "title": it["title"],
        "space": SPACE_LABELS.get(it.get("space") or "main"),
        "category": category_label(it.get("category")),
        "type": it["kind"],
        "source": it["source_url"],
        "author": it["author"],
        "published": str(it["published_at"])[:10] if it["published_at"] else None,
        "saved": str(it["created_at"])[:10],
        "tags": it["tags"],
        "kb_id": iid,
    }
    lines = ["---"] + [f"{k}: {_yaml(v)}" for k, v in fm.items() if v not in (None, [], "")] + ["---", ""]
    body = (it.get("content") or it.get("input_text") or "").strip()
    if it["kind"] == "note":
        # une note : le texte de l'utilisateur d'abord, en entier
        lines += [body or "(vide)"]
        if it["user_note"]:
            lines += ["", f"> **Pourquoi je l'ai gardé :** {it['user_note']}"]
    else:
        lines.append(f"*{KIND_LABELS.get(it['kind'], it['kind'] or 'élément')}*"
                     + (f" — [source]({it['source_url']})" if it["source_url"] else ""))
        if it["user_note"]:
            lines += ["", f"> **Pourquoi je l'ai gardé :** {it['user_note']}"]
    if file_ref:
        lines += ["", f"Fichier d'origine : [{it.get('file_name') or 'fichier'}]({file_ref})"]
    if it["summary"]:
        lines += ["", "## Résumé", it["summary"]]
    if it["key_points"]:
        lines += ["", "## Points clés"] + [f"- {p}" for p in it["key_points"]]
    if it["use_cases"]:
        lines += ["", "## Utile pour"] + [f"- {u}" for u in it["use_cases"]]
    if it["entities"]:
        lines += ["", "## Entités", ", ".join(f"[[{e['name']}]]" for e in it["entities"])]
    if related:
        lines += ["", "## Liés"] + [f"- [[{names[t]}]] — {r}" for t, r in related if t in names]
    if it["kind"] != "note" and body:
        lines += ["", "## Contenu", body]
    return "\n".join(lines)


def export_zip_file(include_files: bool = False) -> str:
    """Écrit l'export dans un fichier temporaire (les fichiers d'origine peuvent peser lourd) et renvoie son chemin."""
    items = db.fetchall(
        """select * from items
           where status = 'ready' or (kind = 'note' and input_text is not null)
           order by created_at""")
    links = db.fetchall("select source_id::text, target_id::text, reason from item_links")
    names: dict[str, str] = {}
    used: set[str] = set()
    for it in items:
        base = _slug(it["title"] or (it.get("input_text") or "")[:60])
        name = base if base.lower() not in used else f"{base} ({str(it['id'])[:6]})"
        used.add(name.lower())
        names[str(it["id"])] = name

    related: dict[str, list[tuple[str, str]]] = {}
    for l in links:
        related.setdefault(l["source_id"], []).append((l["target_id"], l["reason"]))
        related.setdefault(l["target_id"], []).append((l["source_id"], l["reason"]))

    fd, path = tempfile.mkstemp(prefix="kb-export-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for it in items:
                iid = str(it["id"])
                folder = _folder(it)
                file_ref = None
                if include_files and it.get("file_path"):
                    stored = f"KB/Fichiers/{iid[:8]}-{PurePosixPath(it['file_path']).name}"
                    try:
                        z.writestr(stored, storage.download(it["file_path"]))
                        depth = folder.count("/")
                        file_ref = "../" * depth + stored[3:].replace(" ", "%20")
                    except Exception:
                        log.warning("Fichier non exporté : %s", it["file_path"], exc_info=True)
                z.writestr(f"{folder}/{names[iid]}.md", _markdown(it, names, related.get(iid, []), file_ref))
    except BaseException:
        os.unlink(path)
        raise
    return path


def export_zip(include_files: bool = False) -> bytes:
    path = export_zip_file(include_files)
    try:
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)
