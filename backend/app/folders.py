"""Folders: the user's own shelves for the KB (ML, Claude, Interviews, Personal…), one per item.

Claude files every new item in the folder that fits (`llm.enrich`, in `pipeline.process`), and `sort_items` files the
items already there (once at the first start, then from the app's "Sort automatically" button). A folder chosen by
hand (in the app, or in the Shortcut's "Où le ranger ?" list, which `choices()` fills) sticks: `metadata.manual_folder`.
"""

from __future__ import annotations

import logging
import threading
import unicodedata

from . import db, llm

log = logging.getLogger(__name__)

# Created once, at the first start with this feature; the user renames, deletes or adds from there.
DEFAULT_FOLDERS = [
    ("ML", "Machine learning et IA : modèles, LLM, agents, papiers de recherche, entraînement, évaluation, outils et "
           "infrastructure pour l'IA."),
    ("Claude", "Claude et Anthropic : Claude Code, le connecteur MCP, les prompts, astuces et annonces autour de "
               "Claude."),
    ("Entretien", "Préparer des entretiens d'embauche : questions techniques, études de cas, conseils de recrutement "
                  "et de carrière."),
    ("Perso", "Développement personnel : principes, philosophie de vie, livres et méthodes pour mieux vivre, "
              "décider, travailler ; santé, habitudes, finances personnelles."),
]
SEEDED, SORTED = "folders_seeded", "folders_sorted"

# The Shortcut's "Où le ranger ?" list: Claude chooses, one of the folders, or the Perso space (notes about yourself)
AUTO_CHOICE = "Automatique"
PERSO_SPACE_CHOICE = "Espace Perso"
RESERVED = {AUTO_CHOICE, PERSO_SPACE_CHOICE, "Aucun", "Veille"}
MAX_NAME = 60
BATCH = 20

_sorting = threading.Lock()


class FolderError(ValueError):
    """A folder name that can't be used (empty, too long, reserved or taken)."""


def _key(name: str) -> str:
    """Folder names match without case or accents: "entretien" finds "Entretien"."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()


def _flag(key: str) -> bool:
    return bool(db.fetchone("select 1 from kb_settings where key = %s", (key,)))


def _set_flag(key: str) -> None:
    db.execute("""insert into kb_settings (key, value) values (%s, '{"done": true}')
                  on conflict (key) do update set updated_at = now()""", (key,))


def seed() -> None:
    """The default folders, created once: a folder the user deleted doesn't come back."""
    if _flag(SEEDED):
        return
    for i, (name, description) in enumerate(DEFAULT_FOLDERS):
        db.execute("""insert into folders (name, description, position) values (%s, %s, %s)
                      on conflict ((lower(name))) do nothing""", (name, description, i))
    _set_flag(SEEDED)
    log.info("Dossiers par défaut créés")


def startup() -> None:
    """At start (worker side): files the items that were in the KB before folders existed, once."""
    try:
        seed()
        if not _flag(SORTED):
            sort_items()
            _set_flag(SORTED)
    except Exception:  # noqa: BLE001 — tried again at the next start
        log.warning("Rangement des éléments dans les dossiers impossible", exc_info=True)


def all_folders() -> list[dict]:
    return db.fetchall(
        """select f.id::text, f.name, f.description, f.position,
                  count(i.id) filter (where not i.archived)::int as count
             from folders f left join items i on i.folder_id = f.id
            group by f.id order by f.position, lower(f.name)""")


def overview() -> dict:
    return {"folders": all_folders(),
            "unfiled": db.fetchone("select count(*)::int n from items where folder_id is null and not archived")["n"]}


def get(folder_id: str) -> dict | None:
    try:
        return db.fetchone("select id::text, name, description, position from folders where id = %s::uuid",
                           (folder_id,))
    except Exception:  # noqa: BLE001 — not a uuid
        return None


def resolve(value: str | None) -> dict | None:
    """A folder from its id or its name (any case, with or without accents)."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) == 36 and value.count("-") == 4:
        found = get(value)
        if found:
            return found
    for f in db.fetchall("select id::text, name, description, position from folders order by position"):
        if _key(f["name"]) == _key(value):
            return f
    return None


def _clean_name(name: str | None, *, current: str | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise FolderError("Donne un nom au dossier")
    if len(name) > MAX_NAME:
        raise FolderError(f"Nom trop long ({MAX_NAME} caractères max.)")
    if _key(name) in {_key(r) for r in RESERVED}:
        raise FolderError(f"« {name} » est réservé, choisis un autre nom")
    taken = resolve(name)
    if taken and taken["id"] != current:
        raise FolderError(f"Le dossier « {taken['name']} » existe déjà")
    return name


def create(name: str, description: str | None = None) -> dict:
    name = _clean_name(name)
    return db.fetchone(
        """insert into folders (name, description, position)
           values (%s, %s, coalesce((select max(position) + 1 from folders), 0))
           returning id::text, name, description, position""",
        (name, (description or "").strip() or None))


def update(folder_id: str, *, name: str | None = None, description: str | None = None,
           position: int | None = None) -> dict | None:
    folder = get(folder_id)
    if not folder:
        return None
    sets, params = [], []
    if name is not None:
        sets.append("name = %s")
        params.append(_clean_name(name, current=folder["id"]))
    if description is not None:
        sets.append("description = %s")
        params.append(description.strip() or None)
    if position is not None:
        sets.append("position = %s")
        params.append(position)
    if sets:
        db.execute(f"update folders set {', '.join(sets)} where id = %s::uuid", (*params, folder_id))
    return get(folder_id)


def delete(folder_id: str) -> bool:
    """Its items stay in the KB, unfiled; the ones filed there by hand can be sorted again."""
    if not get(folder_id):
        return False
    with db.conn() as c, c.transaction():
        c.execute("update items set metadata = metadata - 'manual_folder' where folder_id = %s::uuid", (folder_id,))
        c.execute("delete from folders where id = %s::uuid", (folder_id,))
    return True


def move(item_id: str, folder_id: str | None) -> bool:
    """Files an item by hand (None: out of any folder). Claude won't move it again."""
    row = db.fetchone(
        """update items set folder_id = %s::uuid, metadata = metadata || '{"manual_folder": true}'
            where id = %s returning id""", (folder_id, item_id))
    return bool(row)


# ---------------------------------------------------------------------------
# Choices for the Shortcut, and what it sends back
# ---------------------------------------------------------------------------

def choices() -> list[str]:
    return [AUTO_CHOICE, *(f["name"] for f in all_folders()), PERSO_SPACE_CHOICE]


def parse_choice(value: str | None) -> tuple[str | None, str | None]:
    """What the Shortcut (or the app) sent as `folder` → (folder id or None, space or None). Automatic or a folder
    that no longer exists: Claude chooses."""
    value = (value or "").strip()
    if not value or _key(value) in (_key(AUTO_CHOICE), "auto"):
        return None, None
    if _key(value) == _key(PERSO_SPACE_CHOICE):
        return None, "perso"
    folder = resolve(value)
    if not folder:
        log.info("Dossier inconnu « %s » : rangement automatique", value)
        return None, None
    return folder["id"], None


# ---------------------------------------------------------------------------
# Automatic filing
# ---------------------------------------------------------------------------

def for_enrich() -> list[dict]:
    """The folders as Claude sees them when filing a new item."""
    return [{"name": f["name"], "description": f["description"]}
            for f in db.fetchall("select name, description from folders order by position, lower(name)")]


def id_for(name: str | None) -> str | None:
    if not name:
        return None
    found = resolve(name)
    return found["id"] if found else None


def sort_items() -> dict:
    """Files again every item not filed by hand, in batches. Returns how many were looked at and filed."""
    if not _sorting.acquire(blocking=False):
        return {"busy": True, "sorted": 0, "filed": 0}
    try:
        folders = for_enrich()
        if not folders:
            return {"busy": False, "sorted": 0, "filed": 0}
        by_name = {f["name"]: f["id"] for f in all_folders()}
        items = db.fetchall(
            """select id::text, kind, coalesce(title, left(input_text, 90)) as title, left(summary, 400) as summary,
                      tags, folder_id::text
                 from items
                where status = 'ready' and not coalesce((metadata->>'manual_folder')::boolean, false)
                order by created_at""")
        filed = 0
        for start in range(0, len(items), BATCH):
            batch = items[start:start + BATCH]
            chosen = llm.classify_folders(batch, folders)
            for it in batch:
                target = by_name.get(chosen.get(it["id"]))
                if target != it["folder_id"]:
                    # skipped if the user filed it by hand meanwhile
                    db.execute("""update items set folder_id = %s::uuid where id = %s
                                  and not coalesce((metadata->>'manual_folder')::boolean, false)""",
                               (target, it["id"]))
                filed += target is not None
        log.info("Rangement : %d éléments, %d dans un dossier", len(items), filed)
        return {"busy": False, "sorted": len(items), "filed": filed}
    finally:
        _sorting.release()
