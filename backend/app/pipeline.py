"""Ingestion (mise en file) et traitement complet d'un item."""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, embeddings, llm, notion, storage, urls
from .chunking import chunk_text
from .config import get_settings
from .extractors import ExtractionError, Extracted, extract_item
from .extractors.web import blocks_servers as web_blocks_servers
from .taxonomy import (KIND_LABELS, category_from_word, category_label, normalize_category, normalize_space,
                       space_from_word)

log = logging.getLogger(__name__)

__all__ = ["KIND_LABELS", "clean_tags", "ingest", "create_note", "process", "fail", "reembed_card"]

# A shared text this long that comes with a link is the page itself (the Safari share sheet attaches the page's text),
# not a snippet: it's kept aside and read when the server can't read the page (see extractors.extract_item).
PAGE_TEXT_MIN = 2000
# How long an item waits for the page the phone fetches (POST /api/items/<id>/page) before the server tries itself.
PAGE_WAIT_SECONDS = 60

INGEST_KIND = {"tweet": "tweet", "youtube": "youtube", "paper": "paper", "repo": "repo", "media": "video", "web": "article"}
# Types dont le titre d'origine est fiable (sinon on prend celui proposé par Claude)
KEEP_SOURCE_TITLE = {"article", "youtube", "video", "audio", "paper", "repo", "pdf"}


# ---------------------------------------------------------------------------
# Mise en file
# ---------------------------------------------------------------------------

def _primary_url(text: str | None) -> str | None:
    """Une URL accompagnée d'un court texte (partage depuis une app) compte comme un lien. Un lien seul sur la
    première ligne suivi d'un long texte aussi : c'est un article collé avec son lien (site qui bloque le serveur)."""
    if not text:
        return None
    found = urls.find_urls(text)
    if len(found) == 1 and len(text.replace(found[0], "").strip()) < 200:
        return found[0]
    first, _, rest = text.strip().partition("\n")
    link = urls.only_url(first)
    return link if link and len(rest.strip()) > PAGE_TEXT_MIN else None


def _safe_filename(name: str) -> str:
    # Supabase Storage refuse les clés non ASCII (« Invalid key ») : « Résumé.pdf » → « Resume.pdf »
    name = unicodedata.normalize("NFKD", name or "fichier").encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.ASCII).strip("._") or "fichier"
    return name[-120:]


_HASHTAG = re.compile(r"(?<![\w#&])#([^\W\d_][\w-]*)")
_SPACE_TAGS = {"perso": "perso", "personnel": "perso", "veille": "main"}


def _route(*texts: str | None, base: str | None = None) -> tuple[str | None, str | None, list[str]]:
    """Hashtags de rangement : #perso (ou #veille), plus une catégorie (#principe, #valeur, #leçon…) en Perso.

    Un espace choisi explicitement (base) l'emporte sur les hashtags. Renvoie l'espace, la catégorie et les hashtags
    effectivement utilisés (les seuls à retirer du texte).
    """
    tag_space, category, space_tags, cat_tags = None, None, [], []
    for text in texts:
        for m in _HASHTAG.finditer(text or ""):
            word = m.group(1).lower()
            if word in _SPACE_TAGS:
                tag_space = _SPACE_TAGS[word]
                space_tags.append((m.group(0), _SPACE_TAGS[word]))
            elif (cat := category_from_word(word)) and not category:
                category = cat
                cat_tags.append(m.group(0))
    space = base or tag_space
    used = [tag for tag, target in space_tags if target == space]
    if space == "perso":
        used += cat_tags
    else:
        # une catégorie seule ne suffit pas (#journal dans un texte partagé n'est pas un rangement)
        category = None
    return space, category, used


def _strip_tags(text: str | None, used: list[str]) -> str | None:
    """Retire les hashtags de rangement (et une espace voisine) sans toucher au reste de la mise en forme."""
    if not text or not used:
        return text
    for tag in used:
        text = re.sub(rf"[ \t]?(?<![\w#&]){re.escape(tag)}(?![\w-])", "", text, count=1)
    return text.strip() or None


def clean_tags(tags: list[str] | None) -> list[str]:
    seen, out = set(), []
    for t in tags or []:
        t = "-".join(str(t).strip().lower().lstrip("#").split())
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:20]


def ingest(
    *,
    url: str | None = None,
    text: str | None = None,
    note: str | None = None,
    file: tuple[bytes, str | None, str | None] | None = None,
    space: str | None = None,
    category: str | None = None,
    title: str | None = None,
    page_follows: bool = False,
) -> dict:
    """`page_follows`: the client (the Shortcut) can fetch the page from the phone. For a site that refuses the server,
    the answer then says `page_wanted`, and the item waits a little for POST /api/items/<id>/page."""
    url = (url or "").strip() or None
    text = (text or "").strip() or None
    note = (note or "").strip() or None
    title = (title or "").strip()[:300] or None
    explicit = normalize_space(space)
    if category and space_from_word(category):
        # the Shortcuts' "Où le ranger ?" list mixes spaces (Veille, Perso) and Perso categories in one field
        explicit, category = explicit or space_from_word(category), None
    category = normalize_category(category)

    # rangement par hashtags : dans la note du partage, ou dans le texte d'une note libre
    space, tag_category, used = _route(note, None if (url or file) else text,
                                       base=explicit or ("perso" if category else None))
    note = _strip_tags(note, used)
    if not (url or file):
        text = _strip_tags(text, used)
    space = space or "main"
    category = (category or tag_category) if space == "perso" else None

    if not url and not file and space != "perso":
        url = _primary_url(text)
    page = None
    if text and url and (text.strip() == url or len(text) > PAGE_TEXT_MIN):
        # the Safari share sheet can attach the whole text of the page: kept aside, used if the site blocks the server
        page = text if len(text) > PAGE_TEXT_MIN else None
        text = None
    metadata = {"manual_title": True} if title else {}

    if file:
        data, filename, mime = file
        if len(data) > get_settings().max_upload_mb * 1024 * 1024:
            raise ValueError(f"Fichier trop volumineux (max {get_settings().max_upload_mb} Mo)")
        item_id = str(uuid.uuid4())
        safe = _safe_filename(filename or "fichier")
        path = storage.upload(f"uploads/{item_id}/{safe}", data, mime)
        row = db.fetchone(
            """insert into items (id, file_path, file_name, file_mime, input_text, user_note, kind, title,
                                  space, category, metadata)
               values (%s, %s, %s, %s, %s, %s, null, %s, %s, %s, %s) returning id, status""",
            (item_id, path, filename, mime, text, note, title or filename, space, category, db.jsonb(metadata)),
        )
        _wake()
        return {"id": str(row["id"]), "status": row["status"], "duplicate": False}

    if url:
        info = urls.classify(url)
        clause, params = urls.dedupe_filter(info)
        existing = db.fetchone(f"select id, status, user_note, space, metadata from items where {clause} limit 1",
                               params)
        retried = False
        if existing and page and existing["status"] == "error":
            # shared again from Safari after the server couldn't read it: this time the phone sends the page
            meta = {**(db.loads(existing["metadata"]) or {}), "page_path": _save_page(str(existing["id"]), page)}
            db.execute("""update items set metadata = %s, status = 'pending', attempts = 0, error = null,
                                next_attempt_at = null, locked_at = null where id = %s""",
                       (db.jsonb(meta), existing["id"]))
            existing["status"], retried = "pending", True
            _wake()
        if existing:
            if note and note not in (existing["user_note"] or ""):
                merged = f"{existing['user_note']}\n{note}" if existing["user_note"] else note
                db.execute("update items set user_note = %s where id = %s", (merged, existing["id"]))
            if space == "perso" and existing["space"] != "perso":   # repartagé exprès vers Perso : on le déplace
                db.execute("update items set space = 'perso', category = coalesce(%s, category) where id = %s",
                           (category, existing["id"]))
            out = {"id": str(existing["id"]), "status": existing["status"], "duplicate": True}
            if retried:
                out["retried"] = True
            elif page_follows and not page and existing["status"] == "error" and info.kind == "web":
                out["page_wanted"] = True          # failed before: the phone sends the page, which requeues it
            return out
        item_id = str(uuid.uuid4())
        if page:
            metadata["page_path"] = _save_page(item_id, page)
        wanted = page_follows and not page and info.kind == "web" and web_blocks_servers(url)
        row = db.fetchone(
            """insert into items (id, input_url, input_text, user_note, source_url, kind, title, space, category,
                                  metadata, next_attempt_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       case when %s then now() + make_interval(secs => %s) end) returning id, status""",
            (item_id, url, text, note, info.canonical, INGEST_KIND.get(info.kind), title, space, category,
             db.jsonb(metadata), wanted, PAGE_WAIT_SECONDS),
        )
        _wake()
        out = {"id": str(row["id"]), "status": row["status"], "duplicate": False}
        return {**out, "page_wanted": True} if wanted else out

    if text:
        return create_note(content=text, title=title, space=space, category=category, why=note)

    raise ValueError("Envoie une URL, un texte ou un fichier")


def today() -> date:
    """Today where the user lives (DIGEST_TIMEZONE), not where the server runs."""
    return datetime.now(ZoneInfo(get_settings().digest_timezone)).date()


def create_note(
    *,
    content: str,
    title: str | None = None,
    space: str | None = "perso",
    category: str | None = None,
    tags: list[str] | None = None,
    why: str | None = None,
    entry_date: date | None = None,
) -> dict:
    """Note écrite à la main (ou dictée) : le texte est gardé tel quel, Claude ajoute résumé, tags et liens."""
    content = (content or "").strip()
    if not content:
        raise ValueError("La note est vide")
    title = (title or "").strip()[:300] or None
    space = normalize_space(space) or "main"
    category = normalize_category(category) if space == "perso" else None
    tags = clean_tags(tags)
    metadata = {}
    if title:
        metadata["manual_title"] = True
    if tags:
        metadata["user_tags"] = tags
    # a journal note belongs to a day: the one chosen in the calendar, today otherwise
    entry_date = (entry_date or today()) if category == "journal" else None
    row = db.fetchone(
        """insert into items (input_text, user_note, kind, title, space, category, tags, metadata, entry_date)
           values (%s, %s, 'note', %s, %s, %s, %s, %s, %s) returning id, status""",
        (content, (why or "").strip() or None, title, space, category, tags, db.jsonb(metadata), entry_date),
    )
    _wake()
    return {"id": str(row["id"]), "status": row["status"], "duplicate": False}


def _save_page(item_id: str, page: str) -> str:
    return storage.upload(f"pages/{item_id}.txt", page[:500_000].encode("utf-8"), "text/plain; charset=utf-8")


MAX_PAGE_BYTES = 15 * 1024 * 1024


def attach_page(item_id: str, html: bytes) -> dict:
    """The page as the phone fetched it (a site that refuses the server): kept, and the item is read (again) now."""
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise LookupError("Élément introuvable") from None
    if not html:
        raise ValueError("Page vide")
    if len(html) > MAX_PAGE_BYTES:
        raise ValueError("Page trop volumineuse")
    row = db.fetchone("select status, metadata from items where id = %s", (item_id,))
    if not row:
        raise LookupError("Élément introuvable")
    path = storage.upload(f"pages/{item_id}.html", html, "text/html")
    meta = {**(db.loads(row["metadata"]) or {}), "page_html_path": path}
    # pending: start now instead of waiting; processing or failed: start again with the page (a run in progress sees
    # its item changed and drops its result); ready: read again only if the server got next to nothing
    redo = row["status"] in ("pending", "processing", "error") or bool(meta.get("thin_content"))
    if redo:
        db.execute("""update items set metadata = %s, status = 'pending', attempts = 0, error = null,
                            next_attempt_at = null, locked_at = null where id = %s""", (db.jsonb(meta), item_id))
        _wake()
    else:
        db.execute("update items set metadata = %s where id = %s", (db.jsonb(meta), item_id))
    return {"ok": True, "status": "pending" if redo else row["status"]}


def _wake() -> None:
    from .worker import wake

    wake.set()


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------

def existing_tags(limit: int = 80) -> list[str]:
    rows = db.fetchall(
        """select tag, count(*) n from items, unnest(tags) tag
           where status = 'ready' group by tag order by n desc, tag limit %s""",
        (limit,),
    )
    return [r["tag"] for r in rows]


def build_card(item: dict) -> str:
    """Fiche résumé indexée (chunk_index = -1) : sert aux recherches larges et aux liens."""
    meta = [KIND_LABELS.get(item.get("kind") or "", item.get("kind") or ""), item.get("author"), item.get("site_name")]
    if item.get("published_at"):
        meta.append(str(item["published_at"])[:10])
    lines = [item.get("title") or "(sans titre)", " · ".join(m for m in meta if m)]
    if item.get("space") == "perso":
        cat = category_label(item.get("category"))
        lines.append("Espace : Perso (développement personnel)" + (f" · Catégorie : {cat}" if cat else ""))
    if item.get("user_note"):
        lines.append(f"Pourquoi je l'ai gardé : {item['user_note']}")
    if item.get("summary"):
        lines.append(f"Résumé : {item['summary']}")
    if item.get("key_points"):
        lines.append("Points clés :\n" + "\n".join(f"- {p}" for p in item["key_points"]))
    if item.get("use_cases"):
        lines.append("Utile pour :\n" + "\n".join(f"- {u}" for u in item["use_cases"]))
    for code, tr in (db.loads(item.get("translations")) or {}).items():
        # the card in the second language too, so a question asked in that language finds it by its words
        if tr.get("title") and tr["title"] != item.get("title"):
            lines.append(f"Title ({code}): {tr['title']}")
        if tr.get("summary"):
            lines.append(f"Summary ({code}): {tr['summary']}")
    if item.get("tags"):
        lines.append("Tags : " + ", ".join(item["tags"]))
    if item.get("entities"):
        lines.append("Entités : " + ", ".join(e["name"] for e in item["entities"]))
    return "\n".join(lines)


def _context_prefix(item: dict) -> str:
    who = item.get("author") or item.get("site_name") or ""
    return f"{item.get('title') or ''} — {who}\n{(item.get('summary') or '')[:300]}\n---\n"


def process(item: dict) -> None:
    item_id = str(item["id"])
    ex: Extracted = extract_item(item)
    if not (ex.content or "").strip() and not ex.title:
        raise ExtractionError("Aucun contenu exploitable")

    metadata = {**(db.loads(item.get("metadata")) or {}), **ex.metadata}
    if ex.thumbnail_bytes:
        try:
            metadata["thumb_path"] = storage.upload(f"thumbs/{item_id}.png", ex.thumbnail_bytes, "image/png")
        except Exception:
            log.warning("Vignette non stockée", exc_info=True)

    space = item.get("space") or "main"
    enr = llm.enrich(
        kind=ex.kind,
        title=item.get("title") if metadata.get("manual_title") else ex.title,
        author=ex.author,
        source_url=ex.source_url or item.get("source_url"),
        published_at=ex.published_at.isoformat() if ex.published_at else None,
        content=ex.content,
        user_note=item.get("user_note"),
        existing_tags=existing_tags(),
        space=space,
        category=item.get("category"),
    )
    title = ex.title if (ex.title and ex.kind in KEEP_SOURCE_TITLE) else (enr.get("title") or ex.title)
    if ex.kind == "tweet" and ex.title:  # Article X : on garde son titre
        title = ex.title
    if metadata.get("manual_title") and item.get("title"):  # titre écrit ou corrigé à la main
        title = item["title"]
    category = item.get("category")
    if not category and space == "perso":
        try:
            category = normalize_category(enr.get("category"))
        except ValueError:
            category = None

    fields = {
        "kind": ex.kind,
        "title": (title or "")[:300] or None,
        "source_url": ex.source_url or item.get("source_url"),
        "author": ex.author,
        "author_url": ex.author_url,
        "site_name": ex.site_name,
        "published_at": ex.published_at,
        "language": enr.get("language") or ex.language,
        "thumbnail_url": ex.thumbnail_url,
        "content": ex.content,
        "summary": enr.get("summary"),
        "key_points": enr.get("key_points") or [],
        "tags": clean_tags((metadata.get("user_tags") or []) + (enr.get("tags") or []))[:12],
        "entities": enr.get("entities") or [],
        "use_cases": enr.get("use_cases") or [],
        "genre": enr.get("genre"),
        "metadata": metadata,
        "user_note": item.get("user_note"),
        "space": space,
        "category": category,
        "translations": _translations(enr, title),
    }

    card = build_card(fields)
    pieces = chunk_text(ex.content)
    prefix = _context_prefix(fields)
    vectors = embeddings.embed([card] + [prefix + p for p in pieces], input_type="document")

    stale = False
    with db.conn() as c, c.transaction():
        # L'utilisateur a pu modifier l'élément pendant le traitement : on relit la ligne, verrouillée
        current = c.execute(
            "select status, locked_at, title, category, tags, user_note, space, metadata from items where id = %s for update",
            (item_id,),
        ).fetchone()
        if not current or current["status"] != "processing" or current["locked_at"] != item.get("locked_at"):
            # supprimé, réécrit (note) ou relancé entre-temps : ce résultat est périmé, le worker repassera
            log.info("Traitement de %s abandonné : l'élément a changé pendant le traitement", item_id)
            return
        keys = ("title", "category", "tags", "user_note", "space")
        card_built_with = [fields.get(k) for k in keys]
        meta_now = db.loads(current["metadata"]) or {}
        fields["user_note"], fields["space"] = current["user_note"], current["space"]
        if meta_now.get("manual_title") and current["title"]:
            fields["title"] = current["title"]
        if meta_now.get("user_tags") is not None:
            fields["tags"] = clean_tags(meta_now["user_tags"] + (enr.get("tags") or []))[:12]
        if current["category"] and current["space"] == "perso":
            fields["category"] = current["category"]
        if current["space"] != "perso":
            fields["category"] = None
        fields["metadata"] = {**fields["metadata"], **{k: meta_now[k] for k in ("manual_title", "user_tags") if k in meta_now}}
        fields["translations"] = _translations(enr, fields["title"])
        stale = card_built_with != [fields.get(k) for k in keys]
        c.execute(
            """update items set kind=%s, title=%s, source_url=%s, author=%s, author_url=%s, site_name=%s,
                      published_at=%s, language=%s, thumbnail_url=%s, content=%s, summary=%s, key_points=%s,
                      tags=%s, entities=%s, use_cases=%s, genre=%s, metadata=%s, category=%s, translations=%s,
                      status='ready', error=null, locked_at=null, next_attempt_at=null
               where id=%s""",
            (
                fields["kind"], fields["title"], fields["source_url"], fields["author"], fields["author_url"],
                fields["site_name"], fields["published_at"], fields["language"], fields["thumbnail_url"],
                fields["content"], fields["summary"], db.jsonb(fields["key_points"]), fields["tags"],
                db.jsonb(fields["entities"]), db.jsonb(fields["use_cases"]), fields["genre"],
                db.jsonb(fields["metadata"]), fields["category"], db.jsonb(fields["translations"]), item_id,
            ),
        )
        c.execute("delete from chunks where item_id = %s", (item_id,))
        with c.cursor() as cur:
            cur.executemany(
                "insert into chunks (item_id, chunk_index, content, embedding) values (%s, %s, %s, %s::vector)",
                [(item_id, -1, card, db.vec(vectors[0]))]
                + [(item_id, i, p, db.vec(v)) for i, (p, v) in enumerate(zip(pieces, vectors[1:]))],
            )
        c.execute("delete from actions where item_id = %s and not done", (item_id,))
        actions = [a for a in (enr.get("action_items") or []) if a.get("text")]
        if actions:
            with c.cursor() as cur:
                cur.executemany(
                    "insert into actions (item_id, text, kind) values (%s, %s, %s)",
                    [(item_id, a["text"], a.get("kind")) for a in actions],
                )

    if stale:
        reembed_card(item_id)      # fiche indexée refaite avec le titre, les tags ou la note modifiés entre-temps
    try:
        link_item(item_id, fields)
    except Exception:
        log.warning("Calcul des liens impossible pour %s", item_id, exc_info=True)
    notion.wake()


def _translations(enr: dict, title: str | None) -> dict:
    """Second-language card from the enrichment. Its title only when ours is Claude's: a source title (article,
    video, paper) or one the user wrote stays the same in every language."""
    out = {}
    for code, tr in (enr.get("translations") or {}).items():
        tr = dict(tr)
        if title != enr.get("title"):
            tr.pop("title", None)
        if tr:
            out[code] = tr
    return out


def link_item(item_id: str, fields: dict) -> None:
    s = get_settings()
    sims = db.fetchall("select * from similar_items(%s, 8, %s)", (item_id, s.link_min_similarity))
    if not sims:
        return
    dupes = [str(r["item_id"]) for r in sims if r["similarity"] >= 0.96]
    if dupes:
        db.execute(
            "update items set metadata = metadata || %s where id = %s",
            (db.jsonb({"possible_duplicate_of": dupes}), item_id),
        )
    by_id = {str(r["item_id"]): r["similarity"] for r in sims}
    candidates = db.fetchall(
        "select id::text, title, summary from items where id = any(%s::uuid[])", (list(by_id),)
    )
    related = llm.explain_links({"title": fields["title"], "summary": fields["summary"]}, candidates)
    related = [r for r in related if r["id"] in by_id][: s.max_links_per_item]
    db.execute("delete from item_links where source_id = %s", (item_id,))
    for r in related:
        db.execute(
            """insert into item_links (source_id, target_id, similarity, reason) values (%s, %s, %s, %s)
               on conflict (source_id, target_id) do update set similarity = excluded.similarity, reason = excluded.reason""",
            (item_id, r["id"], by_id[r["id"]], r["reason"]),
        )


def fail(item: dict, exc: Exception) -> None:
    s = get_settings()
    permanent = isinstance(exc, ExtractionError) or item.get("attempts", 0) >= s.max_attempts
    # an ExtractionError is written for the user (what happened, what to do); anything else keeps its type for debugging
    message = (str(exc) if isinstance(exc, ExtractionError) else f"{type(exc).__name__}: {exc}")[:1000]
    # sans effet si l'élément a été réécrit ou relancé pendant le traitement (il est déjà de nouveau en file)
    still_ours = "status = 'processing' and locked_at is not distinct from %s"
    if permanent:
        db.execute(f"update items set status='error', error=%s, locked_at=null where id=%s and {still_ours}",
                   (message, item["id"], item.get("locked_at")))
    else:
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=2 * max(1, item.get("attempts", 1)))
        db.execute(
            f"update items set status='pending', error=%s, locked_at=null, next_attempt_at=%s where id=%s and {still_ours}",
            (message, retry_at, item["id"], item.get("locked_at")),
        )


def reembed_card(item_id: str) -> None:
    """Après modification manuelle (note, tags) : met à jour la fiche indexée."""
    item = db.fetchone("select * from items where id = %s", (item_id,))
    if not item or item["status"] != "ready":
        return
    card = build_card(item)
    vector = embeddings.embed([card])[0]
    with db.conn() as c, c.transaction():
        c.execute("delete from chunks where item_id = %s and chunk_index = -1", (item_id,))
        c.execute(
            "insert into chunks (item_id, chunk_index, content, embedding) values (%s, -1, %s, %s::vector)",
            (item_id, card, db.vec(vector)),
        )
    notion.wake()
