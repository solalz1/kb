"""Copie automatique de la KB dans une base Notion : une sauvegarde lisible hors de Supabase, et un export prêt à l'emploi.

L'app reste la source de vérité : chaque élément a une page dans la base Notion, réécrite quand l'élément change
(une retouche faite directement dans Notion est écrasée à la modification suivante). Facultatif : sans NOTION_TOKEN
ni NOTION_PARENT_PAGE_ID, rien ne se passe.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone

import httpx

from . import db
from .config import get_settings
from .taxonomy import CATEGORIES, KIND_LABELS, SPACE_LABELS, category_label

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
MIN_INTERVAL = 0.35        # Notion accepte ~3 requêtes/s en moyenne par intégration
BATCH = 100                # éléments copiés par passe
SWEEP_EVERY = 600          # secondes entre deux passes quand rien ne réveille la synchro
MAX_BODY = 60_000          # caractères de contenu copiés pour un élément capturé (une note est copiée en entier)
SHORT_BODY = 8_000         # repli si Notion refuse un contenu trop long ou trop complexe
STATE_KEY = "notion"
LIVE = "(status = 'ready' or (status = 'error' and kind = 'note' and input_text is not null))"

wake_event = threading.Event()
_pass_lock = threading.Lock()
_syncer: Syncer | None = None


class NotionError(Exception):
    def __init__(self, status: int, code: str | None, message: str):
        super().__init__(f"Notion {status} {code or ''} : {message}".replace("  ", " "))
        self.status, self.code = status, code


class DataSourceGone(NotionError):
    """La base Notion a été supprimée, ou l'intégration n'y a plus accès."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PAGE_ID = re.compile(r"(?<![0-9a-f])([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f])", re.I)


def parent_page_id() -> str | None:
    """Accepte l'ID de la page ou son URL (https://www.notion.so/Ma-page-1a2b…?pvs=4)."""
    raw = (get_settings().notion_parent_page_id or "").strip().split("?")[0].split("#")[0]
    found = _PAGE_ID.findall(raw)
    if not found:
        return None
    h = found[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def enabled() -> bool:
    return bool(get_settings().notion_token and parent_page_id())


def wake() -> None:
    if enabled():
        wake_event.set()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None
_throttle = threading.Lock()
_last_call = 0.0
_sleep = time.sleep   # remplacé dans les tests


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=API, timeout=60)
    return _client


def _request(method: str, path: str, body: dict | None = None) -> dict:
    global _last_call
    headers = {"Authorization": f"Bearer {get_settings().notion_token}", "Notion-Version": VERSION}
    status, message = 0, "Notion ne répond pas"
    for attempt in range(6):
        with _throttle:
            delay = _last_call + MIN_INTERVAL - time.monotonic()
            if delay > 0:
                _sleep(delay)
            _last_call = time.monotonic()
        try:
            r = _http().request(method, path, json=body, headers=headers)
        except httpx.TransportError as exc:
            status, message = 0, str(exc)
            _sleep(min(30, 2 ** attempt))
            continue
        if r.status_code in (409, 429) or r.status_code >= 500:
            status, message = r.status_code, r.text[:300]
            try:
                wait = float(r.headers.get("retry-after") or 2 ** attempt)
            except ValueError:
                wait = 2 ** attempt
            _sleep(min(60, wait))
            continue
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            raise NotionError(r.status_code, data.get("code"), data.get("message") or r.text[:300])
        return r.json() if r.content else {}
    raise NotionError(status, "unavailable", message)


# ---------------------------------------------------------------------------
# État (identifiants de la base créée, dernière synchro) dans la table kb_settings
# ---------------------------------------------------------------------------

def get_state() -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (STATE_KEY,))
    return dict(row["value"]) if row else {}


def _save_state(**changes) -> dict:
    row = db.fetchone(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = kb_settings.value || excluded.value, updated_at = now()
           returning value""",
        (STATE_KEY, db.jsonb(changes)),
    )
    return dict(row["value"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _forget_pages() -> None:
    """Nouvelle base Notion : toutes les pages seront recréées."""
    db.execute("update items set notion_page_id = null, notion_synced_at = null "
               "where notion_page_id is not null or notion_synced_at is not null")
    db.execute("delete from notion_trash")


# ---------------------------------------------------------------------------
# Base et pages
# ---------------------------------------------------------------------------

def _schema() -> dict:
    def select(names) -> dict:
        return {"type": "select", "select": {"options": [{"name": n} for n in names]}}

    return {
        "Nom": {"type": "title", "title": {}},
        "Espace": select(SPACE_LABELS.values()),
        "Catégorie": select(label for label, _, _ in CATEGORIES.values()),
        "Type": select(KIND_LABELS.values()),
        "Tags": {"type": "multi_select", "multi_select": {"options": []}},
        "Résumé": {"type": "rich_text", "rich_text": {}},
        "Source": {"type": "url", "url": {}},
        "Auteur": {"type": "rich_text", "rich_text": {}},
        "Publié": {"type": "date", "date": {}},
        "Ajouté": {"type": "date", "date": {}},
        "Archivé": {"type": "checkbox", "checkbox": {}},
        "Fiche KB": {"type": "url", "url": {}},
        "ID KB": {"type": "rich_text", "rich_text": {}},
    }


def ensure_database() -> dict:
    state = get_state()
    parent = parent_page_id()
    if state.get("data_source_id") and state.get("parent_page_id") == parent:
        return state
    if state.get("data_source_id") or state.get("parent_page_id"):
        _forget_pages()
    try:
        created = _request("POST", "/databases", {
            "parent": {"type": "page_id", "page_id": parent},
            "title": [{"type": "text", "text": {"content": "Knowledge base"}}],
            "icon": {"type": "emoji", "emoji": "🗂️"},
            "initial_data_source": {"properties": _schema()},
        })
    except NotionError as exc:
        if exc.status in (403, 404):
            raise NotionError(exc.status, exc.code, "page parente introuvable : partage-la avec ton intégration "
                                                    "(menu ··· de la page > Connexions)") from exc
        raise
    sources = created.get("data_sources") or []
    if not sources:
        raise NotionError(500, "no_data_source", "Notion n'a pas renvoyé de source de données pour la base créée")
    log.info("Base Notion créée : %s", created.get("url"))
    return _save_state(parent_page_id=parent, database_id=created["id"], data_source_id=sources[0]["id"],
                       url=created.get("url"), created_at=_now())


def _text(value, limit: int = 2000) -> list[dict]:
    value = str(value or "").strip()
    return [{"type": "text", "text": {"content": value[:limit]}}] if value else []


def _url(value: str | None) -> str | None:
    return value if value and len(value) <= 2000 and value.startswith(("http://", "https://")) else None


def page_properties(it: dict) -> dict:
    cat = category_label(it.get("category"))
    created = it.get("created_at")
    return {
        "Nom": {"title": _text(it.get("title") or "(sans titre)")},
        "Espace": {"select": {"name": SPACE_LABELS.get(it.get("space") or "main", "Veille")}},
        "Catégorie": {"select": {"name": cat} if cat else None},
        "Type": {"select": {"name": KIND_LABELS[it["kind"]]} if it.get("kind") in KIND_LABELS else None},
        "Tags": {"multi_select": [{"name": str(t).replace(",", " ")[:100]} for t in (it.get("tags") or [])[:30]]},
        "Résumé": {"rich_text": _text(it.get("summary"))},
        "Source": {"url": _url(it.get("source_url"))},
        "Auteur": {"rich_text": _text(it.get("author"), 500)},
        "Publié": {"date": {"start": str(it["published_at"])[:10]} if it.get("published_at") else None},
        "Ajouté": {"date": {"start": created.isoformat() if isinstance(created, datetime) else str(created)}
                   if created else None},
        "Archivé": {"checkbox": bool(it.get("archived"))},
        "Fiche KB": {"url": _url(get_settings().item_url(str(it["id"])))},
        "ID KB": {"rich_text": _text(it["id"])},
    }


# Le Markdown de Notion interprète des balises (<page>, <callout>, <mention-…>) : on neutralise les chevrons
_TAG_LIKE = re.compile(r"<(?=[A-Za-z/!])")


def _md(text: str | None) -> str:
    return _TAG_LIKE.sub(r"\\<", (text or "").strip())


def page_markdown(it: dict, max_body: int = MAX_BODY) -> str:
    is_note = it.get("kind") == "note"
    body = (it.get("content") or it.get("input_text") or "").strip()
    fiche: list[str] = []
    if it.get("user_note"):
        fiche.append("> **Pourquoi je l'ai gardé :** " + _md(it["user_note"]).replace("\n", "\n> "))
    if it.get("summary"):
        fiche += ["## Résumé", _md(it["summary"])]
    if it.get("key_points"):
        fiche += ["## Points clés", "\n".join(f"- {_md(p)}" for p in it["key_points"])]
    if it.get("use_cases"):
        fiche += ["## Utile pour", "\n".join(f"- {_md(u)}" for u in it["use_cases"])]
    if it.get("entities"):
        fiche += ["## Personnes, outils, concepts", _md(", ".join(e["name"] for e in it["entities"] if e.get("name")))]
    if is_note:
        # la note de l'utilisateur d'abord, en entier ; la fiche générée ensuite
        parts = [_md(body)] + (["---"] + fiche if fiche else [])
    else:
        parts = fiche
        if body:
            cut = body[:max_body]
            parts += ["## Contenu", _md(cut)]
            if len(body) > max_body:
                parts.append(f"*[… contenu tronqué : {len(body) - max_body} caractères de plus dans la fiche KB]*")
    if it.get("status") == "error":
        parts.append("*(Fiche pas encore générée : traitement en erreur dans l'app.)*")
    return "\n\n".join(p for p in parts if p) or "*(vide)*"


def _markdown_variants(it: dict) -> list[str]:
    """Contenu complet, puis une version courte si Notion refuse la première (trop longue ou trop complexe)."""
    full = page_markdown(it)
    if it.get("kind") == "note":
        note = (it.get("content") or it.get("input_text") or "").strip()
        short = (_md(note[:SHORT_BODY]) + "\n\n*[… note tronquée dans Notion : version complète dans l'app "
                 "et dans l'export]*") if len(note) > SHORT_BODY else page_markdown({**it, "key_points": [], "entities": []})
    else:
        short = page_markdown(it, max_body=SHORT_BODY)
    return [full] if short == full else [full, short]


def _create_page(state: dict, it: dict) -> str:
    base = {"parent": {"type": "data_source_id", "data_source_id": state["data_source_id"]},
            "properties": page_properties(it)}
    attempts = _markdown_variants(it) + [""]
    for i, md in enumerate(attempts):
        try:
            page = _request("POST", "/pages", {**base, "markdown": md} if md else base)
            return page["id"]
        except NotionError as exc:
            if exc.status == 404:
                raise DataSourceGone(exc.status, exc.code, str(exc)) from exc
            if exc.status != 400 or i == len(attempts) - 1:
                raise
            log.info("Notion refuse le contenu de %s (%s), essai plus court", it["id"], exc)
    raise AssertionError("inaccessible")


def _write_markdown(page_id: str, it: dict) -> None:
    attempts = _markdown_variants(it)
    for i, md in enumerate(attempts):
        try:
            _request("PATCH", f"/pages/{page_id}/markdown",
                     {"type": "replace_content", "replace_content": {"new_str": md}})
            return
        except NotionError as exc:
            if exc.status != 400 or i == len(attempts) - 1:
                raise


def upsert_page(state: dict, it: dict) -> str:
    page_id = it.get("notion_page_id")
    if page_id:
        try:
            _request("PATCH", f"/pages/{page_id}", {"properties": page_properties(it), "in_trash": False})
        except NotionError as exc:
            if exc.status != 404:
                raise
            log.info("Page Notion %s supprimée : recréée", page_id)
        else:
            _write_markdown(page_id, it)
            return page_id
    return _create_page(state, it)


def _trash(page_id: str | None) -> None:
    if not page_id:
        return
    try:
        _request("PATCH", f"/pages/{page_id}", {"in_trash": True})
    except NotionError as exc:
        if exc.status != 404:
            raise


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------

def sync_pending(limit: int = BATCH) -> dict:
    """Une passe : corbeille des éléments supprimés, puis copie des éléments nouveaux ou modifiés."""
    if not enabled():
        return {"enabled": False, "synced": 0, "trashed": 0, "failed": 0, "remaining": 0}
    with _pass_lock:
        state = ensure_database()
        spaces = get_settings().notion_space_list

        trashed, last_error = 0, None
        for row in db.fetchall("select page_id from notion_trash order by created_at limit %s", (limit,)):
            try:
                _trash(row["page_id"])
            except NotionError as exc:      # on réessaiera à la passe suivante, sans bloquer la copie
                last_error = f"corbeille de la page {row['page_id']} : {exc}"
                log.warning("Page Notion %s non mise à la corbeille : %s", row["page_id"], exc)
                continue
            db.execute("delete from notion_trash where page_id = %s", (row["page_id"],))
            trashed += 1

        rows = db.fetchall(
            f"""select *, id::text as id from items
                where notion_synced_at is null and {LIVE} and (space = any(%s) or notion_page_id is not null)
                order by created_at limit %s""",
            (spaces, limit),
        )
        synced, failed = 0, 0
        for it in rows:
            try:
                if it["space"] in spaces:
                    page_id = upsert_page(state, it)
                else:                       # espace exclu de la copie (NOTION_SPACES) : la page part à la corbeille
                    _trash(it["notion_page_id"])
                    page_id = None
            except DataSourceGone:
                log.warning("Base Notion introuvable : elle sera recréée")
                _forget_pages()
                _save_state(data_source_id=None, database_id=None, url=None)
                wake_event.set()
                break
            except NotionError as exc:
                failed += 1
                last_error = f"« {it.get('title') or it['id']} » : {exc}"
                log.warning("Copie Notion impossible pour %s : %s", it["id"], exc)
                continue
            saved = db.fetchone(
                """update items set notion_page_id = %s,
                          notion_synced_at = case when updated_at = %s then now() else null end
                   where id = %s returning id""",
                (page_id, it["updated_at"], it["id"]),
            )
            if not saved and page_id:
                # supprimé dans l'app pendant la copie : sa page ne doit pas rester dans Notion
                db.execute("insert into notion_trash (page_id) values (%s) on conflict do nothing", (page_id,))
                wake_event.set()
            synced += 1

        remaining = db.fetchone(
            f"select count(*)::int n from items where notion_synced_at is null and {LIVE} and space = any(%s)",
            (spaces,),
        )["n"]
        _save_state(last_sync_at=_now(), last_error=last_error,
                    last_error_at=_now() if last_error else state.get("last_error_at"))
        return {"enabled": True, "synced": synced, "trashed": trashed, "failed": failed, "remaining": remaining}


def status() -> dict:
    s = get_settings()
    out: dict = {"configured": enabled(), "spaces": s.notion_space_list}
    if not out["configured"]:
        out["missing"] = [name for name, ok in (("NOTION_TOKEN", s.notion_token),
                                                ("NOTION_PARENT_PAGE_ID", parent_page_id())) if not ok]
        return out
    state = get_state()
    counts = db.fetchone(
        f"""select count(*) filter (where notion_page_id is not null and notion_synced_at is not null)::int as synced,
                   count(*) filter (where notion_synced_at is null and {LIVE} and space = any(%s))::int as pending
            from items""",
        (s.notion_space_list,),
    )
    out.update(counts)
    out.update({k: state.get(k) for k in ("url", "last_sync_at", "last_error", "last_error_at")})
    return out


def _record_error(exc: Exception) -> None:
    try:
        _save_state(last_error=str(exc)[:500], last_error_at=_now())
    except Exception:
        log.warning("État Notion non enregistré", exc_info=True)


def request_sync() -> None:
    """« Synchroniser maintenant » : réveille la synchro du worker, ou lance une passe si aucun worker ne tourne ici."""
    if _syncer is not None and _syncer.alive:
        wake_event.set()
        return

    def run():
        try:
            while sync_pending()["synced"] >= BATCH:
                pass
        except Exception as exc:
            log.warning("Synchro Notion échouée", exc_info=True)
            _record_error(exc)

    threading.Thread(target=run, name="kb-notion-once", daemon=True).start()


class Syncer:
    """Thread de fond : copie les changements dès qu'on le réveille, et toutes les 10 minutes sinon."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        global _syncer
        _syncer = self
        self._thread = threading.Thread(target=self._loop, name="kb-notion", daemon=True)
        self._thread.start()
        log.info("Copie Notion activée")

    def stop(self) -> None:
        self._stop.set()
        wake_event.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            more = False
            try:
                res = sync_pending()
                more = res["synced"] > 0 and res["remaining"] > 0
            except Exception as exc:
                log.warning("Synchro Notion échouée : %s", exc)
                _record_error(exc)
            if not more:
                wake_event.wait(timeout=SWEEP_EVERY)
                wake_event.clear()
                self._stop.wait(2)   # regroupe les modifications rapprochées
