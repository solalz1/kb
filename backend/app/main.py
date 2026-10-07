"""API HTTP : ingestion (Raccourci), lecture, chat, export, front PWA et connecteur MCP."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import chat, costs, db, export, llm, migrate, notion, pipeline, search, storage
from .config import get_settings
from .digest import agent as digest_agent
from .digest import following as digest_following
from .digest import profile as digest_profile
from .digest import render as digest_render
from .digest import sources as digest_sources
from .mcp_server import mcp, resurface_items
from .taxonomy import CATEGORIES, SPACE_LABELS, normalize_category, normalize_space
from .worker import Worker, wake

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kb")
settings = get_settings()

STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parents[2] / "web" / "dist"))

_mcp_http = None


def _build_mcp_http():
    """L'app MCP (et son gestionnaire de sessions) est recréée à chaque démarrage de l'API."""
    global _mcp_http
    _mcp_http = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # l'accès est protégé par le secret d'URL / le jeton, pas par le contrôle d'en-tête Host
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # the database first: the code that was just deployed may need a new column
    await run_in_threadpool(migrate.run_at_startup)
    worker = None
    if settings.run_worker:
        worker = Worker()
        worker.start()
    _build_mcp_http()
    async with mcp.session_manager.run():
        yield
    if worker:
        worker.stop()
    db.close()


api = FastAPI(title="KB", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
if settings.cors_origin_list:
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# The Shortcuts show the response's "message" in a notification: errors carry one too, so a failed share says why
# instead of an empty notification. "detail" stays for the app.
def _error(status: int, detail: Any, headers: dict | None = None) -> JSONResponse:
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    return JSONResponse({"detail": detail, "message": f"Erreur : {text}"[:500]}, status_code=status, headers=headers)


@api.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, exc.detail, getattr(exc, "headers", None))


@api.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e.get("loc", ())[1:]) or "corps" for e in exc.errors())
    return _error(422, f"requête invalide ({fields})")


@api.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    log.exception("%s %s failed", request.method, request.url.path)
    return _error(500, f"erreur serveur ({type(exc).__name__}: {exc})"[:300])


# ---------------------------------------------------------------------------
# Authentification : un jeton unique (Bearer), partagé par le Raccourci et l'app
# ---------------------------------------------------------------------------

def _token_ok(value: str | None) -> bool:
    expected = settings.kb_api_token
    return bool(expected and value and secrets.compare_digest(value.strip(), expected))


def require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-kb-token")
    if not _token_ok(token):
        raise HTTPException(status_code=401, detail="Jeton invalide ou manquant")


auth = [Depends(require_token)]


# ---------------------------------------------------------------------------
# Santé
# ---------------------------------------------------------------------------

@api.get("/api/health")
async def health():
    try:
        await run_in_threadpool(db.fetchone, "select 1 as ok")
        db_ok = True
    except Exception:
        db_ok = False
    # "ok" only depends on the database (Railway's health check); "storage" tells why file shares would fail
    return {"ok": db_ok, "db": db_ok, "auth_configured": bool(settings.kb_api_token),
            "schema": migrate.status, "storage": await run_in_threadpool(storage.check)}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def _str(v: Any) -> str | None:
    if v is None or isinstance(v, UploadFile):
        return None
    v = str(v).strip()
    return v or None


@api.post("/api/ingest", dependencies=auth)
async def ingest(request: Request):
    ctype = request.headers.get("content-type", "")
    results = []
    try:
        if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
            form = await request.form()
            url, text, note = _str(form.get("url")), _str(form.get("text")), _str(form.get("note"))
            extra = {k: _str(form.get(k)) for k in ("space", "category", "title")}
            files = [f for key in ("file", "files", "file[]") for f in form.getlist(key) if isinstance(f, UploadFile)]
            for f in files:
                data = await f.read()
                if not data:
                    continue
                results.append(await run_in_threadpool(
                    pipeline.ingest, note=note, text=text, file=(data, f.filename, f.content_type), **extra))
            if not files and (url or text):
                results.append(await run_in_threadpool(pipeline.ingest, url=url, text=text, note=note, **extra))
        elif "application/json" in ctype:
            body = await request.json()
            body = body if isinstance(body, dict) else {}
            results.append(await run_in_threadpool(
                pipeline.ingest, url=_str(body.get("url")), text=_str(body.get("text")), note=_str(body.get("note")),
                space=_str(body.get("space")), category=_str(body.get("category")), title=_str(body.get("title"))))
        else:
            raw = (await request.body()).decode("utf-8", errors="replace")
            results.append(await run_in_threadpool(pipeline.ingest, text=raw))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not results:
        raise HTTPException(status_code=400, detail="Rien à ajouter : envoie une URL, un texte ou un fichier")

    dupes = sum(r["duplicate"] for r in results)
    if len(results) == 1:
        message = "Déjà dans ta KB ✓" if dupes else "Ajouté à ta KB ✓"
    else:
        message = f"{len(results) - dupes} élément(s) ajouté(s)" + (f", {dupes} déjà présent(s)" if dupes else "")
    return {"ok": True, "message": message, "items": results, "id": results[0]["id"]}


class NoteIn(BaseModel):
    content: str
    title: str | None = None
    space: str = "perso"
    category: str | None = None
    tags: list[str] | None = None
    entry_date: date | None = None     # journal notes: the day they belong to (default: today)


@api.post("/api/notes", dependencies=auth)
def create_note(note: NoteIn):
    """Note écrite dans l'app (espace Perso par défaut)."""
    try:
        res = pipeline.create_note(content=note.content, title=note.title, space=note.space,
                                   category=note.category, tags=note.tags, entry_date=note.entry_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "id": res["id"], "status": res["status"], "message": "Note ajoutée à ta KB ✓"}


@api.get("/api/taxonomy", dependencies=auth)
def taxonomy():
    return {
        "spaces": [{"id": k, "label": v} for k, v in SPACE_LABELS.items()],
        "categories": [{"id": k, "label": label, "plural": plural, "description": desc}
                       for k, (label, plural, desc) in CATEGORIES.items()],
    }


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

LIST_FIELDS = """id::text, kind, status, error, coalesce(title, left(input_text, 90)) as title, source_url, author,
                 site_name, published_at, created_at, left(coalesce(summary, input_text), 320) as summary, tags,
                 thumbnail_url, user_note, pinned, archived, file_path, file_mime,
                 metadata->>'thumb_path' as thumb_path, genre, space, category, translations, entry_date"""


def _with_thumbs(rows: list[dict]) -> list[dict]:
    paths = []
    for r in rows:
        if r.get("thumb_path"):
            paths.append(r["thumb_path"])
        elif r.get("kind") == "image" and r.get("file_path"):
            paths.append(r["file_path"])
    signed = storage.signed_urls(paths)
    for r in rows:
        own = r.get("thumb_path") or (r.get("file_path") if r.get("kind") == "image" else None)
        r["thumbnail"] = signed.get(own) if own else r.get("thumbnail_url")
        r.pop("thumb_path", None)
    return rows


@api.get("/api/items", dependencies=auth)
def list_items(
    q: str | None = None,
    kind: str | None = None,
    tag: str | None = None,
    entity: str | None = None,
    status: str | None = None,
    pinned: bool | None = None,
    archived: bool = False,
    space: str | None = None,
    category: str | None = None,
    limit: int = 40,
    offset: int = 0,
):
    limit = max(1, min(limit, 100))
    try:
        space, category = normalize_space(space), normalize_category(category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if q and q.strip():
        found = search.search_items(q.strip(), limit=limit, kinds=[kind] if kind else None, tags=[tag] if tag else None,
                                    spaces=[space] if space else None)
        if category:
            found = [f for f in found if f.get("category") == category]
        ids = [f["id"] for f in found]
        rows = db.fetchall(f"select {LIST_FIELDS} from items where id = any(%s::uuid[])", (ids,)) if ids else []
        order = {iid: i for i, iid in enumerate(ids)}
        excerpts = {f["id"]: (f.get("excerpts") or [None])[0] for f in found}
        rows.sort(key=lambda r: order[r["id"]])
        for r in rows:
            r["excerpt"] = excerpts.get(r["id"])
        return {"items": _with_thumbs(rows), "total": len(rows), "search": True}

    where, params = ["archived = %s"], [archived]
    if space:
        where.append("space = %s")
        params.append(space)
    if category:
        where.append("category = %s")
        params.append(category)
    if kind:
        where.append("kind = %s")
        params.append(kind)
    if tag:
        where.append("%s = any(tags)")
        params.append(tag)
    if entity:
        where.append("entities @> %s")
        params.append(db.jsonb([{"name": entity}]))
    if status:
        where.append("status = %s")
        params.append(status)
    if pinned is not None:
        where.append("pinned = %s")
        params.append(pinned)
    clause = " and ".join(where)
    rows = db.fetchall(
        f"select {LIST_FIELDS} from items where {clause} order by pinned desc, created_at desc limit %s offset %s",
        (*params, limit, offset),
    )
    total = db.fetchone(f"select count(*) n from items where {clause}", params)["n"]
    return {"items": _with_thumbs(rows), "total": total, "search": False}


def _links(item_id: str) -> list[dict]:
    rows = db.fetchall(
        """select i.id::text, i.title, i.kind, i.source_url, l.reason, l.similarity
           from item_links l
           join items i on i.id = case when l.source_id = %(id)s then l.target_id else l.source_id end
           where (l.source_id = %(id)s or l.target_id = %(id)s) and i.status = 'ready'
           order by l.similarity desc limit 12""",
        {"id": item_id},
    )
    seen, out = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


@api.get("/api/items/{item_id}", dependencies=auth)
def get_item(item_id: str):
    it = db.fetchone(
        """update items set view_count = view_count + 1, last_viewed_at = now() where id = %s
           returning *, id::text as id""",
        (item_id,),
    )
    if not it:
        raise HTTPException(404, "Élément introuvable")
    meta = it.get("metadata") or {}
    it["file_url"] = storage.signed_url(it.get("file_path"))
    it["thumbnail"] = storage.signed_url(meta.get("thumb_path")) or (
        it["file_url"] if it.get("kind") == "image" else it.get("thumbnail_url"))
    it["links"] = _links(item_id)
    it["actions"] = db.fetchall("select id, text, kind, done from actions where item_id = %s order by id", (item_id,))
    it.pop("locked_at", None)
    return it


class ItemPatch(BaseModel):
    title: str | None = None
    user_note: str | None = None
    tags: list[str] | None = None
    pinned: bool | None = None
    archived: bool | None = None
    space: str | None = None
    category: str | None = None
    content: str | None = None    # notes uniquement : le texte est remplacé puis retraité
    entry_date: date | None = None  # journal : déplace la note vers un autre jour


@api.patch("/api/items/{item_id}", dependencies=auth)
def patch_item(item_id: str, patch: ItemPatch):
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return {"ok": True}
    item = db.fetchone("select kind, input_url, file_path, title from items where id = %s", (item_id,))
    if not item:
        raise HTTPException(404, "Élément introuvable")

    sets, params, meta = [], [], {}
    try:
        space = (normalize_space(changes["space"]) or "main") if "space" in changes else None
        category = normalize_category(changes["category"]) if "category" in changes else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if space:
        sets.append("space = %s")
        params.append(space)
    if space == "main":
        sets.append("category = null")          # les catégories n'ont de sens que dans l'espace Perso
    elif "category" in changes:
        sets.append("category = %s")
        params.append(category)
        if category == "journal" and not changes.get("entry_date"):
            # filed in the journal: it belongs to the day it was written
            sets.append("entry_date = coalesce(entry_date, (created_at at time zone %s)::date)")
            params.append(settings.digest_timezone)
    if changes.get("entry_date"):
        sets.append("entry_date = %s")
        params.append(changes["entry_date"])
    if "title" in changes:
        title = (changes["title"] or "").strip()[:300] or None
        sets.append("title = %s")
        params.append(title)
        # a title written by hand is the same in every language: drop the translated one
        sets.append("""translations = coalesce((select jsonb_object_agg(key, value - 'title')
                                                from jsonb_each(translations)), '{}'::jsonb)""")
        meta["manual_title"] = bool(title)
    if "user_note" in changes:
        sets.append("user_note = %s")
        params.append((changes["user_note"] or "").strip() or None)
    if "tags" in changes:
        tags = pipeline.clean_tags(changes["tags"])
        sets.append("tags = %s")
        params.append(tags)
        meta["user_tags"] = tags
    for flag in ("pinned", "archived"):
        if changes.get(flag) is not None:
            sets.append(f"{flag} = %s")
            params.append(changes[flag])
    requeue = False
    if "content" in changes:
        if item["kind"] != "note" or item["input_url"] or item["file_path"]:
            raise HTTPException(400, "Seules les notes peuvent être réécrites")
        content = (changes["content"] or "").strip()
        if not content:
            raise HTTPException(400, "La note est vide")
        sets += ["input_text = %s", "content = %s", "status = 'pending'", "attempts = 0", "error = null",
                 "next_attempt_at = null", "locked_at = null"]
        params += [content, content]
        requeue = True
    if meta:
        sets.append("metadata = metadata || %s")
        params.append(db.jsonb(meta))
    if sets:
        db.execute(f"update items set {', '.join(sets)} where id = %s", (*params, item_id))

    if requeue:
        wake.set()      # résumé, tags, index et liens refaits par le worker
    elif {"title", "user_note", "tags", "space", "category"} & changes.keys():
        threading.Thread(target=pipeline.reembed_card, args=(item_id,), daemon=True).start()
    notion.wake()
    return {"ok": True, "requeued": requeue}


@api.delete("/api/items/{item_id}", dependencies=auth)
def delete_item(item_id: str):
    row = db.fetchone(
        """with gone as (delete from items where id = %s returning file_path, metadata, notion_page_id),
                trash as (insert into notion_trash (page_id) select notion_page_id from gone
                          where notion_page_id is not null on conflict do nothing)
           select * from gone""",
        (item_id,),
    )
    if not row:
        raise HTTPException(404, "Élément introuvable")
    notion.wake()
    for path in (row.get("file_path"), (row.get("metadata") or {}).get("thumb_path")):
        try:
            storage.delete(path)
        except Exception:
            log.warning("Fichier non supprimé : %s", path)
    return {"ok": True}


@api.post("/api/items/{item_id}/reprocess", dependencies=auth)
def reprocess(item_id: str):
    row = db.fetchone(
        """update items set status = 'pending', attempts = 0, error = null, next_attempt_at = null, locked_at = null
           where id = %s returning id""",
        (item_id,),
    )
    if not row:
        raise HTTPException(404, "Élément introuvable")
    wake.set()
    return {"ok": True}


def _space_param(space: str | None) -> str | None:
    try:
        return normalize_space(space)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.get("/api/tags", dependencies=auth)
def tags(limit: int = 200, space: str | None = None):
    space = _space_param(space)
    return db.fetchall(
        """select tag, count(*)::int as count from items, unnest(tags) tag
           where status = 'ready' and not archived and (%s::text is null or space = %s)
           group by tag order by count desc, tag limit %s""",
        (space, space, limit),
    )


@api.get("/api/entities", dependencies=auth)
def entities(limit: int = 100, type: str | None = None, space: str | None = None):
    space = _space_param(space)
    return db.fetchall(
        """select e->>'name' as name, e->>'type' as type, count(*)::int as count
           from items, jsonb_array_elements(entities) e
           where status = 'ready' and not archived and (%s::text is null or e->>'type' = %s)
             and (%s::text is null or space = %s)
           group by 1, 2 having count(*) >= 1 order by count desc, name limit %s""",
        (type, type, space, space, limit),
    )


@api.get("/api/categories", dependencies=auth)
def categories():
    """Nombre d'éléments par catégorie dans l'espace Perso."""
    rows = db.fetchall(
        """select category, count(*)::int as count from items
           where space = 'perso' and not archived and category is not null group by category""")
    counts = {r["category"]: r["count"] for r in rows}
    return [{"id": k, "label": label, "plural": plural, "count": counts.get(k, 0)}
            for k, (label, plural, _) in CATEGORIES.items()]


@api.get("/api/actions", dependencies=auth)
def actions(include_done: bool = False):
    return db.fetchall(
        """select a.id, a.text, a.kind, a.done, a.created_at, i.id::text as item_id, i.title as item_title, i.kind as item_kind,
                  i.source_url from actions a join items i on i.id = a.item_id
           where (%s or not a.done) order by a.done, a.created_at desc limit 300""",
        (include_done,),
    )


class ActionPatch(BaseModel):
    done: bool


@api.patch("/api/actions/{action_id}", dependencies=auth)
def patch_action(action_id: int, patch: ActionPatch):
    db.execute(
        "update actions set done = %s, done_at = case when %s then now() else null end where id = %s",
        (patch.done, patch.done, action_id),
    )
    return {"ok": True}


@api.get("/api/resurface", dependencies=auth)
def resurface(count: int = 4, space: str | None = None):
    rows = resurface_items(max(1, min(count, 12)), _space_param(space))
    ids = [r["id"] for r in rows]
    full = db.fetchall(f"select {LIST_FIELDS} from items where id = any(%s::uuid[])", (ids,)) if ids else []
    return _with_thumbs(full)


@api.get("/api/stats", dependencies=auth)
def stats():
    by_kind = db.fetchall("select kind, count(*)::int n from items where status='ready' group by kind order by n desc")
    by_status = db.fetchall("select status, count(*)::int n from items group by status")
    week = db.fetchone("select count(*)::int n from items where created_at > now() - interval '7 days'")
    open_actions = db.fetchone("select count(*)::int n from actions where not done")
    by_space = db.fetchall("select space, count(*)::int n from items where not archived group by space")
    return {
        "by_space": {r["space"]: r["n"] for r in by_space},
        "by_kind": by_kind,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "total": sum(r["n"] for r in by_status),
        "this_week": week["n"],
        "open_actions": open_actions["n"],
    }


# ---------------------------------------------------------------------------
# Journal: Perso notes of category 'journal', one calendar day each
# ---------------------------------------------------------------------------

JOURNAL_WHERE = "space = 'perso' and category = 'journal' and not archived"


def _journal_day_sql() -> str:
    # notes filed in the journal before the calendar existed: the day they were written
    return "coalesce(entry_date, (created_at at time zone %(tz)s)::date)"


@api.get("/api/journal", dependencies=auth)
def journal_month(month: str | None = None):
    """Number of journal notes per day of `month` (YYYY-MM, default: this month)."""
    today = pipeline.today()
    try:
        first = date.fromisoformat(f"{month}-01") if month else today.replace(day=1)
        if not 1900 <= first.year <= 9000:
            raise ValueError(month)
    except ValueError as exc:
        raise HTTPException(400, "Mois invalide : attendu AAAA-MM") from exc
    after = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    day = _journal_day_sql()
    rows = db.fetchall(
        f"""select {day} as day, count(*)::int as n from items
            where {JOURNAL_WHERE} and {day} >= %(first)s and {day} < %(after)s group by 1 order by 1""",
        {"tz": settings.digest_timezone, "first": first, "after": after},
    )
    return {"month": first.isoformat()[:7], "today": today.isoformat(),
            "days": {r["day"].isoformat(): r["n"] for r in rows}}


@api.get("/api/journal/{day}", dependencies=auth)
def journal_day(day: date):
    """The day's journal notes, in the order they were written: the user's own text, whatever their processing."""
    rows = db.fetchall(
        f"""select id::text, kind, status, title, coalesce(input_text, summary, title) as text, source_url,
                   created_at, updated_at, {_journal_day_sql()} as day
            from items where {JOURNAL_WHERE} and {_journal_day_sql()} = %(day)s order by created_at""",
        {"tz": settings.digest_timezone, "day": day},
    )
    return {"date": day.isoformat(), "entries": rows}


# ---------------------------------------------------------------------------
# Chat (Server-Sent Events)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict]
    mode: str = "ask"            # ask | project | advice
    kinds: list[str] | None = None
    tags: list[str] | None = None
    model: str | None = None     # un des modèles de /api/models ; défaut : CHAT_MODEL
    lang: str | None = None      # langue de l'app (fr, en) : celle de la réponse ; défaut : KB_LANGUAGE


@api.get("/api/models", dependencies=auth)
def models():
    return settings.chat_model_options


@api.post("/api/chat", dependencies=auth)
def chat_endpoint(req: ChatRequest):
    if not req.messages or not str(req.messages[-1].get("content", "")).strip():
        raise HTTPException(400, "Message vide")
    allowed = {o["id"] for o in settings.chat_model_options}
    if req.model and req.model not in allowed:
        raise HTTPException(400, f"Modèle non proposé : {req.model}")
    model = req.model or settings.chat_model
    if req.mode not in ("ask", "project", "advice"):
        raise HTTPException(400, f"Mode inconnu : {req.mode}")
    lang = req.lang if req.lang in llm.LANG_NAMES else None

    def events():
        try:
            if req.mode == "project":
                gen = chat.project(str(req.messages[-1]["content"]), kinds=req.kinds, model=model, lang=lang)
            elif req.mode == "advice":
                gen = chat.advise(req.messages, model=model, lang=lang)
            else:
                gen = chat.ask(req.messages, kinds=req.kinds, tags=req.tags, model=model, lang=lang)
            for event in gen:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            log.exception("Erreur du chat")
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})



# ---------------------------------------------------------------------------
# Tech digest agent: digests, interests, followed people and feeds
# ---------------------------------------------------------------------------

DIGEST_LIST_FIELDS = """id, kind, period_start, period_end, status, headline, error, created_at, updated_at,
    jsonb_array_length(coalesce(data->'projects', '[]'::jsonb)) as n_projects,
    (select count(*) from jsonb_array_elements(coalesce(data->'entries', '[]'::jsonb)) e
      where not coalesce((e->>'hidden')::boolean, false))::int as n_entries"""


def _digest_out(row: dict) -> dict:
    row = dict(row)
    row["title"] = digest_render.title_for(row)
    row["feedback"] = db.fetchall(
        "select target, entry_key, vote, item_id::text from digest_feedback where digest_id = %s", (row["id"],))
    return row


@api.get("/api/digests", dependencies=auth)
def list_digests(kind: str | None = None, limit: int = 30):
    rows = db.fetchall(
        f"""select {DIGEST_LIST_FIELDS} from digests where (%s::text is null or kind = %s)
            order by period_start desc, kind desc limit %s""",
        (kind, kind, max(1, min(limit, 100))),
    )
    for r in rows:
        r["title"] = digest_render.title_for(r)
    return rows


@api.get("/api/digests/latest", dependencies=auth)
def latest_digest(kind: str = "daily"):
    row = db.fetchone("select * from digests where kind = %s order by period_start desc limit 1", (kind,))
    return _digest_out(row) if row else None


@api.get("/api/digests/{digest_id}", dependencies=auth)
def get_digest(digest_id: int):
    row = digest_agent.get(digest_id)
    if not row:
        raise HTTPException(404, "Digest introuvable")
    return _digest_out(row)


class DigestRequest(BaseModel):
    kind: str = "daily"


@api.post("/api/digests/generate", dependencies=auth)
def generate_digest(req: DigestRequest):
    if req.kind not in ("daily", "weekly"):
        raise HTTPException(400, "kind : daily ou weekly")
    if not settings.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY manquant")
    return {"ok": True, "id": digest_agent.start_now(req.kind)}


@api.post("/api/digests/{digest_id}/projects", dependencies=auth)
def more_projects(digest_id: int):
    if not digest_agent.get(digest_id):
        raise HTTPException(404, "Digest introuvable")
    if not digest_agent.start_more_projects(digest_id):
        raise HTTPException(409, "Digest pas encore prêt, ou recherche d'idées déjà en cours")
    return {"ok": True}


@api.post("/api/digests/{digest_id}/regenerate", dependencies=auth)
def regenerate_digest(digest_id: int):
    if not digest_agent.get(digest_id):
        raise HTTPException(404, "Digest introuvable")
    if not settings.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY manquant")
    if not digest_agent.regenerate(digest_id):
        raise HTTPException(409, "Ce digest est déjà en cours d'écriture")
    return {"ok": True, "id": digest_id}


class FeedbackIn(BaseModel):
    target: str          # entry | project
    key: str
    vote: int            # 1, -1, 2 (garder / je le fais), 0 (annuler)


@api.post("/api/digests/{digest_id}/feedback", dependencies=auth)
def digest_feedback(digest_id: int, fb: FeedbackIn):
    if fb.target not in ("entry", "project") or fb.vote not in (-1, 0, 1, 2):
        raise HTTPException(400, "Retour invalide")
    try:
        return digest_agent.feedback(digest_id, fb.target, fb.key, fb.vote)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@api.get("/api/interests", dependencies=auth)
def interests():
    watch = db.fetchall("select * from watch order by status, kind, origin, name")
    return {
        "profile": digest_profile.get(),
        "text": digest_profile.manual_text(),
        "people": [w for w in watch if w["kind"] == "person"],
        "feeds": [w for w in watch if w["kind"] == "feed"],
        "schedule": {
            "enabled": settings.digest_enabled, "hour": settings.digest_hour, "timezone": settings.digest_timezone,
            "email": digest_render.email_enabled(), "x": bool(settings.x_bearer_token and settings.digest_x_max_posts),
        },
        "x_follow": digest_following.public_state(),
    }


@api.get("/api/costs", dependencies=auth)
def costs_summary():
    """What the KB cost this month and in all, by service, and what is left on prepaid accounts."""
    return costs.summary()


class CostSettingsIn(BaseModel):
    balance: float | None = None       # what the service's console shows now (prepaid credits)
    clear_balance: bool = False
    before: float | None = None        # spent before the KB measured it
    monthly: float | None = None       # a fixed monthly plan


@api.put("/api/costs/{service}", dependencies=auth)
def set_costs(service: str, body: CostSettingsIn):
    for value in (body.balance, body.before, body.monthly):
        if value is not None and value < 0:
            raise HTTPException(400, "Montant négatif")
    try:
        return costs.set_service(service, balance=body.balance, before=body.before, monthly=body.monthly,
                                 clear_balance=body.clear_balance)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class XAccountIn(BaseModel):
    username: str


def _following(fn, *args):
    try:
        return fn(*args)
    except digest_following.FollowError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.put("/api/x-follow", dependencies=auth)
def link_x_account(body: XAccountIn):
    """Link the user's X account: the people they follow from now on join the digest's people."""
    if not body.username.strip():
        return digest_following.unlink()
    return _following(digest_following.link, body.username)


@api.post("/api/x-follow/sync", dependencies=auth)
def sync_x_follows():
    return _following(digest_following.sync)


@api.post("/api/x-follow/import", dependencies=auth)
def import_x_follows():
    """Every account followed so far, at the cost shown in the app."""
    return _following(digest_following.import_all)


class InterestsText(BaseModel):
    text: str


@api.put("/api/interests", dependencies=auth)
def set_interests(body: InterestsText):
    digest_profile.set_manual_text(body.text)
    return {"ok": True}


@api.post("/api/interests/refresh", dependencies=auth)
def refresh_interests():
    if not settings.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY manquant")
    return digest_profile.compute(force=True)


class WatchIn(BaseModel):
    name: str | None = None
    x_handle: str | None = None
    url: str | None = None
    note: str | None = None


@api.post("/api/watch", dependencies=auth)
def add_watch(w: WatchIn):
    """Follow a person (X handle and/or site) or a feed (site or RSS URL; the feed is discovered automatically)."""
    handle = (w.x_handle or "").strip().lstrip("@").split("/")[-1] or None
    if handle and not re.fullmatch(r"\w{1,15}", handle):
        raise HTTPException(400, "Identifiant X invalide")
    url = (w.url or "").strip() or None
    if not handle and not url:
        raise HTTPException(400, "Donne un compte X ou l'adresse d'un site ou d'un flux")
    feed_url = None
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            feed_url = digest_sources.discover_feed(url)
        except Exception:
            feed_url = None
        if not feed_url and not handle:
            raise HTTPException(400, "Aucun flux RSS trouvé à cette adresse")
    kind = "person" if handle else "feed"
    name = (w.name or "").strip() or handle or url
    existing = db.fetchone(
        "select id from watch where (%s::text is not null and lower(x_handle) = lower(%s)) or (%s::text is not null and feed_url = %s)",
        (handle, handle, feed_url, feed_url))
    if existing:
        row = db.fetchone(
            """update watch set status = 'active', name = coalesce(%s, name), url = coalesce(%s, url),
                      feed_url = coalesce(%s, feed_url), note = coalesce(%s, note) where id = %s returning *""",
            ((w.name or "").strip() or None, url, feed_url, w.note, existing["id"]))
    else:
        row = db.fetchone(
            """insert into watch (kind, name, x_handle, url, feed_url, origin, status, note)
               values (%s, %s, %s, %s, %s, 'manual', 'active', %s) returning *""",
            (kind, name, handle, url, feed_url, w.note))
    return row


class WatchPatch(BaseModel):
    status: str | None = None
    name: str | None = None


@api.patch("/api/watch/{watch_id}", dependencies=auth)
def patch_watch(watch_id: int, patch: WatchPatch):
    if patch.status and patch.status not in ("active", "muted", "suggested"):
        raise HTTPException(400, "Statut invalide")
    row = db.fetchone(
        "update watch set status = coalesce(%s, status), name = coalesce(%s, name) where id = %s returning *",
        (patch.status, (patch.name or "").strip() or None, watch_id))
    if not row:
        raise HTTPException(404, "Source introuvable")
    return row


@api.delete("/api/watch/{watch_id}", dependencies=auth)
def delete_watch(watch_id: int):
    # a person is muted rather than deleted, so the agent doesn't follow them again from the saved tweets
    db.execute("delete from watch where id = %s and kind = 'feed'", (watch_id,))
    db.execute("update watch set status = 'muted' where id = %s and kind = 'person'", (watch_id,))
    return {"ok": True}

# ---------------------------------------------------------------------------
# Export & fichiers locaux (dev)
# ---------------------------------------------------------------------------

@api.get("/api/export", dependencies=auth)
def export_markdown(files: bool = False, lang: str | None = None):
    """Markdown (Obsidian, ou Notion : Importer > Markdown) ; files=true ajoute les fichiers d'origine ;
    lang = langue des fiches (fr, en ; défaut : KB_LANGUAGE)."""
    path = export.export_zip_file(include_files=files, lang=lang)
    return FileResponse(path, media_type="application/zip", filename="kb-export.zip",
                        background=BackgroundTask(os.unlink, path))


@api.get("/api/notion", dependencies=auth)
def notion_status():
    return notion.status()


class NotionLanguage(BaseModel):
    language: str


@api.put("/api/notion/language", dependencies=auth)
def notion_language(body: NotionLanguage):
    """Langue de la copie Notion. En changer recrée la base dans cette langue et y recopie tout."""
    try:
        return notion.set_language(body.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/api/notion/sync", dependencies=auth)
def notion_sync():
    if not notion.enabled():
        raise HTTPException(400, "Copie Notion non configurée : ajoute NOTION_TOKEN et NOTION_PARENT_PAGE_ID")
    notion.request_sync()
    return {"ok": True}


@api.get("/api/local-files/{path:path}")
def local_file(path: str, sig: str):
    if not secrets.compare_digest(sig, storage.local_signature(path)):
        raise HTTPException(403, "Signature invalide")
    base = Path(settings.local_storage_dir).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.exists():
        raise HTTPException(404)
    return FileResponse(target)


# ---------------------------------------------------------------------------
# Front PWA (build Vite) — servi par la même app
# ---------------------------------------------------------------------------

if (STATIC_DIR / "assets").exists():
    api.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@api.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    # .well-known : pas d'OAuth ici (l'accès MCP passe par le secret d'URL) → 404 explicite, pas la page de l'app
    if full_path.startswith(("api/", "mcp", ".well-known/")):
        raise HTTPException(404)
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        headers = {"Cache-Control": "no-cache"} if candidate.name in ("sw.js", "manifest.webmanifest") else None
        return FileResponse(candidate, headers=headers)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"message": "API KB en ligne. Front non buildé (voir web/)."})


# ---------------------------------------------------------------------------
# Application racine : aiguille /mcp vers le serveur MCP, le reste vers l'API
# ---------------------------------------------------------------------------

def _noindex(send):
    """Demande aux moteurs de recherche de ne rien indexer (en plus de robots.txt)."""
    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = dict(message, headers=[*message.get("headers", []), (b"x-robots-tag", b"noindex, nofollow")])
        await send(message)
    return wrapped


class RootApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            send = _noindex(send)
            path = scope["path"].rstrip("/")
            secret = settings.kb_mcp_secret
            if path == "/mcp" or path.startswith("/mcp/"):
                if secret and path == f"/mcp/{secret}":
                    ok = True                                  # connecteur Claude : secret dans l'URL
                else:
                    headers = dict(scope.get("headers") or [])
                    value = headers.get(b"authorization", b"").decode()
                    token = value[7:] if value.lower().startswith("bearer ") else ""
                    ok = path == "/mcp" and bool(
                        (secret and token and secrets.compare_digest(token, secret)) or _token_ok(token))
                if not ok:
                    return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                if _mcp_http is None:
                    return await JSONResponse({"error": "starting"}, status_code=503)(scope, receive, send)
                return await _mcp_http(dict(scope, path="/mcp", raw_path=b"/mcp"), receive, send)
        return await api(scope, receive, send)


app = RootApp()
