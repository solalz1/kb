"""The wiki: Markdown pages Claude writes and keeps up to date from the items (Karpathy's "LLM Wiki" pattern).

Three layers. The items are the raw sources: never changed here. The pages (`wiki_pages`) are the wiki: one per theme,
concept, entity or book, never one per item, in five sections; Claude owns them, the user reads them (and may edit
them in Obsidian, see obsidian.py: his version then wins). The schema is the conventions below plus the user's own
(`kb_settings.wiki_schema`), sent with every call.

Operations, all through one queue (`wiki_jobs`) worked by one thread, so two jobs never rewrite a page at once:
- ingest: each item that becomes ready is integrated into the pages it concerns (llm.wiki_plan, then llm.wiki_write);
- answer: an answer worth keeping becomes a page;
- lint: every night, Claude reviews the wiki (contradictions, stale claims, duplicates, links, orphans) and suggests
  what to dig next.
Every write is a compare-and-swap on `revision`, keeps the previous version in `wiki_revisions`, and goes to the log.
"""

from __future__ import annotations

import logging
import re

import psycopg
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, llm
from .config import get_settings
from .formatting import item_meta_line

log = logging.getLogger(__name__)

# key -> (French label, English label, what goes there: for Claude)
SECTIONS: dict[str, tuple[str, str, str]] = {
    "personal": ("Personnel", "Personal",
                 "toi : objectifs, principes, valeurs, leçons, habitudes, santé, psychologie, journal ; une image "
                 "structurée de toi qui s'affine avec le temps"),
    "research": ("Recherche", "Research",
                 "les sujets creusés sur la durée (ML, LLM, agents, ingénierie…) : concepts, méthodes, résultats, "
                 "personnes, outils, avec une thèse qui évolue au fil des sources"),
    "reading": ("Lectures", "Reading",
                "les livres lus : une page par livre (idées, chapitres, personnages, thèmes) et des pages transverses "
                "par thème ou par auteur"),
    "business": ("Business", "Business",
                 "ses activités et projets professionnels : clients, offres, prospection, vente, méthodes, décisions, "
                 "chiffres"),
    "competitive": ("Concurrence", "Competitive analysis",
                    "les entreprises, produits et marchés qu'il suit : positionnement, chiffres, annonces, "
                    "comparaisons"),
}
KINDS = ("theme", "entity", "book", "synthesis", "answer")
KIND_LABELS = {"theme": "thème", "entity": "entité", "book": "livre", "synthesis": "synthèse", "answer": "réponse"}

SCHEMA_KEY = "wiki_schema"
SUGGESTIONS_KEY = "wiki_suggestions"
LINT_KEY = "wiki_lint"
LEASE_KEY = "wiki_lease"
LEASE_SECONDS = 30 * 60      # renewed between jobs and between the review's batches; a job is taken over after 60 min
MAX_SOURCE_CHARS = 40_000
LINT_BATCH_CHARS = 40_000
MAX_ATTEMPTS = 3

PAGE_FIELDS = """id::text, title, slug, section, kind, summary, content, links, revision, updated_by, archived, metadata,
                 created_at, updated_at"""

# [[Title]], [[Title|alias]], [[Title#heading]] — the target is what's before | or #
LINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(#[^\[\]|\n]*)?(\|[^\[\]\n]*)?\]\]")
CITE_RE = re.compile(r"\[src:([0-9a-f]{8})\]", re.I)
_FORBIDDEN = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')


class Conflict(Exception):
    """The page changed since it was read (or its title is taken): the job runs again."""


# ---------------------------------------------------------------------------
# Titles, slugs, links
# ---------------------------------------------------------------------------

def clean_title(title: str | None) -> str:
    """A title that is also a file name in Obsidian and a [[link]] target."""
    t = unicodedata.normalize("NFC", str(title or "")).strip()
    t = _FORBIDDEN.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:120].strip() or "Sans titre"


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80].strip("-") or "page"


def links_of(content: str) -> list[str]:
    """Lowercased titles a page links to, in order, once each."""
    seen, out = set(), []
    for m in LINK_RE.finditer(content or ""):
        target = m.group(1).strip().lower()
        if target.startswith("sources/"):
            continue
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def cites_of(content: str) -> list[str]:
    return sorted({m.group(1).lower() for m in CITE_RE.finditer(content or "")})


def ref(item_id: str) -> str:
    """How a page cites an item: the first 8 characters of its id."""
    return str(item_id).replace("-", "")[:8].lower()


def section_label(key: str, lang: str = "fr") -> str:
    fr, en, _ = SECTIONS.get(key, SECTIONS["research"])
    return en if lang == "en" else fr


def section_from_label(label: str | None) -> str | None:
    """'Recherche', 'Research' or 'research' -> 'research'."""
    if not label:
        return None
    norm = unicodedata.normalize("NFKD", str(label)).encode("ascii", "ignore").decode().strip().lower()
    for key, (fr, en, _) in SECTIONS.items():
        for name in (key, fr, en):
            if unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower() == norm:
                return key
    return None


def _normalize_content(content: str | None, title: str | None = None) -> str:
    """Trimmed, Unix line ends, without a first heading that only repeats the page's title (the title is the page's
    name, and the file's): any other first heading stays."""
    text = (content or "").replace("\r\n", "\n").strip()
    if title and text.startswith("# "):
        first, _, rest = text.partition("\n")
        if clean_title(first[2:]).lower() == clean_title(title).lower():
            text = rest.strip()
    return text


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def all_pages(*, with_content: bool = False) -> list[dict]:
    fields = PAGE_FIELDS if with_content else PAGE_FIELDS.replace("content, ", "")
    return db.fetchall(
        f"""select {fields}, (select count(*)::int from wiki_page_sources s where s.page_id = p.id) as n_sources
            from wiki_pages p where not archived order by section, lower(title)""")


def by_title(title: str, c=None) -> dict | None:
    sql = f"select {PAGE_FIELDS} from wiki_pages where lower(title) = lower(%s) and not archived"
    return (c.execute(sql, (clean_title(title),)).fetchone() if c else db.fetchone(sql, (clean_title(title),)))


def get_page(key: str) -> dict | None:
    """A page by slug, id or title."""
    page = db.fetchone(f"select {PAGE_FIELDS} from wiki_pages where slug = %s and not archived", (key,))
    if not page:
        try:
            uuid.UUID(key)
            page = db.fetchone(f"select {PAGE_FIELDS} from wiki_pages where id = %s", (key,))
        except ValueError:
            pass
    return page or by_title(key)


def page_sources(page_id: str) -> list[dict]:
    from .search import ITEM_FIELDS

    return db.fetchall(
        f"""select {ITEM_FIELDS} from items where id in (select item_id from wiki_page_sources where page_id = %s)
            order by coalesce(published_at, created_at) desc""", (page_id,))


def backlinks(title: str) -> list[dict]:
    return db.fetchall(
        """select id::text, title, slug, section, summary from wiki_pages
           where not archived and %s = any(links) and lower(title) <> %s order by lower(title)""",
        (title.lower(), title.lower()))


def resolve_titles(titles: list[str]) -> dict[str, str]:
    """Lowercased title -> slug, for the [[links]] of a page that lead somewhere."""
    if not titles:
        return {}
    rows = db.fetchall("select lower(title) t, slug from wiki_pages where not archived and lower(title) = any(%s)",
                       ([t.lower() for t in titles],))
    return {r["t"]: r["slug"] for r in rows}


def resolve_refs(refs: list[str], c=None) -> dict[str, dict]:
    """8-character refs -> the items they cite (id, title, kind, source_url, created_at)."""
    if not refs:
        return {}
    sql = """select id::text, title, kind, source_url, author, published_at, created_at, space, category
             from items where left(replace(id::text, '-', ''), 8) = any(%s)"""
    rows = c.execute(sql, (refs,)).fetchall() if c else db.fetchall(sql, (refs,))
    return {ref(r["id"]): r for r in rows}


def index_text(pages: list[dict] | None = None) -> str:
    """The index as Claude reads it: every page, one line each, by section."""
    pages = all_pages() if pages is None else pages
    out = []
    for key in SECTIONS:
        rows = [p for p in pages if p["section"] == key]
        if not rows:
            continue
        out.append(f"## {section_label(key)} ({key})")
        out += [f"- {p['title']} [{p['kind']}] — {p.get('summary') or ''} ({p.get('n_sources', 0)} sources)".rstrip()
                for p in rows]
    return "\n".join(out)


def schema_text() -> str:
    """The conventions Claude follows, with the user's own at the end."""
    sections = "\n".join(f"- {key} ({fr}) : {desc}" for key, (fr, _, desc) in SECTIONS.items())
    own = user_schema()
    return ("Sections du wiki :\n" + sections + "\n\nTypes de page : theme (un thème, un chapitre, une méthode), "
            "entity (une personne, un outil, une organisation), book (un livre), synthesis (vue d'ensemble d'un "
            "domaine), answer (une réponse gardée)."
            + (f"\n\nConventions de l'utilisateur (prioritaires) :\n{own}" if own else ""))


def user_schema() -> str:
    row = db.fetchone("select value from kb_settings where key = %s", (SCHEMA_KEY,))
    return str((row["value"] or {}).get("text") or "").strip() if row else ""


def set_user_schema(text: str, *, source: str = "app") -> str:
    text = (text or "").strip()[:20_000]
    if text == user_schema():
        return text
    db.execute("""insert into kb_settings (key, value) values (%s, %s)
                  on conflict (key) do update set value = excluded.value, updated_at = now()""",
               (SCHEMA_KEY, db.jsonb({"text": text})))
    add_log("edit", "Conventions du wiki modifiées", {"source": source})
    _wake_sync()
    return text


# ---------------------------------------------------------------------------
# Writing (compare-and-swap)
# ---------------------------------------------------------------------------

def _unique_slug(c, title: str, page_id: str | None = None) -> str:
    base = slugify(title)
    slug, n = base, 2
    while c.execute("select 1 from wiki_pages where slug = %s and not archived and id::text is distinct from %s",
                    (slug, page_id)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def save_page(c, *, page: dict | None, title: str, section: str, kind: str, summary: str | None, content: str,
              author: str, reason: str, add_sources: tuple[str, ...] = (), metadata: dict | None = None) -> dict:
    """Creates the page (page=None) or rewrites it if it is still at the revision that was read. Within the caller's
    transaction `c`. Raises Conflict if it changed meanwhile or if the title belongs to another page."""
    title = clean_title(title)
    section = section if section in SECTIONS else (page or {}).get("section") or "research"
    kind = kind if kind in KINDS else (page or {}).get("kind") or "theme"
    content = _normalize_content(content, title)
    summary = (summary or "").strip()[:400] or None
    links = links_of(content)
    other = c.execute("select id::text from wiki_pages where lower(title) = lower(%s) and not archived",
                      (title,)).fetchone()
    if other and (page is None or other["id"] != page["id"]):
        raise Conflict(f"Une autre page s'appelle déjà « {title} »")
    if page is None:
        try:
            with c.transaction():       # a savepoint: a page created at the same moment elsewhere is a Conflict
                row = c.execute(
                    f"""insert into wiki_pages (title, slug, section, kind, summary, content, links, updated_by, metadata)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning {PAGE_FIELDS}""",
                    (title, _unique_slug(c, title), section, kind, summary, content, links, author,
                     db.jsonb({k: v for k, v in (metadata or {}).items() if v is not None}))).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise Conflict(f"Une autre page s'appelle déjà « {title} »") from exc
    else:
        slug = page["slug"] if title.lower() == page["title"].lower() else _unique_slug(c, title, page["id"])
        meta = {**(db.loads(page.get("metadata")) or {}), **(metadata or {})}
        meta = {k: v for k, v in meta.items() if v is not None}      # None removes a key
        row = c.execute(
            f"""update wiki_pages set title = %s, slug = %s, section = %s, kind = %s, summary = %s, content = %s,
                       links = %s, updated_by = %s, metadata = %s, revision = revision + 1, updated_at = now()
                where id = %s and revision = %s and not archived returning {PAGE_FIELDS}""",
            (title, slug, section, kind, summary, content, links, author, db.jsonb(meta), page["id"],
             page["revision"])).fetchone()
        if not row:
            raise Conflict(f"La page « {page['title']} » a changé pendant la mise à jour")
    c.execute("""insert into wiki_revisions (page_id, revision, title, section, summary, content, author, reason)
                 values (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (row["id"], row["revision"], row["title"], row["section"], row["summary"], row["content"], author,
               reason))
    cited = [r["id"] for r in resolve_refs(cites_of(content), c).values()]
    sources = list(dict.fromkeys([*cited, *add_sources]))
    if sources:
        c.execute("""insert into wiki_page_sources (page_id, item_id)
                     select %s, i.id from items i where i.id = any(%s::uuid[]) on conflict do nothing""",
                  (row["id"], sources))
    return row


def archive_page(c, page: dict, *, reason: str, merged_into: dict | None = None) -> None:
    """Out of the wiki (kept with its history). A merged page hands its sources and its incoming links over."""
    done = c.execute("""update wiki_pages set archived = true, updated_at = now(), revision = revision + 1,
                               metadata = metadata || %s where id = %s and revision = %s returning id""",
                     (db.jsonb({"archived_reason": reason, **({"merged_into": merged_into["id"]} if merged_into else {})}),
                      page["id"], page["revision"])).fetchone()
    if not done:
        raise Conflict(f"La page « {page['title']} » a changé pendant la mise à jour")
    if merged_into:
        c.execute("""insert into wiki_page_sources (page_id, item_id)
                     select %s, item_id from wiki_page_sources where page_id = %s on conflict do nothing""",
                  (merged_into["id"], page["id"]))
        rename_links(c, page["title"], merged_into["title"], reason=reason)


def rename_links(c, old: str, new: str, *, reason: str) -> int:
    """[[old]] becomes [[new]] in every page that links to it (an alias stays, a heading too)."""
    pattern = re.compile(r"\[\[" + re.escape(old) + r"((?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?)\]\]", re.I)
    rows = c.execute(f"select {PAGE_FIELDS} from wiki_pages where not archived and %s = any(links)",
                     (old.lower(),)).fetchall()
    for p in rows:
        content = pattern.sub(lambda m: f"[[{new}{m.group(1)}]]", p["content"])
        if content != p["content"]:
            save_page(c, page=p, title=p["title"], section=p["section"], kind=p["kind"], summary=p["summary"],
                      content=content, author="claude", reason=reason)
    return len(rows)


def add_log(kind: str, title: str, detail: dict | None = None, c=None) -> None:
    sql = "insert into wiki_log (kind, title, detail) values (%s, %s, %s)"
    params = (kind, (title or "")[:300], db.jsonb(detail or {}))
    if c:
        c.execute(sql, params)
    else:
        db.execute(sql, params)


def recent_log(limit: int = 50) -> list[dict]:
    return db.fetchall("select id, at, kind, title, detail from wiki_log order by at desc, id desc limit %s",
                       (max(1, min(limit, 500)),))


def log_markdown(limit: int = 300) -> str:
    """log.md: one entry per event, `## [date] kind | title`, so `grep "^## \\[" log.md` lists them."""
    lines = ["# Journal du wiki", ""]
    for e in recent_log(limit):
        d = e["detail"] or {}
        lines.append(f"## [{str(e['at'])[:10]}] {e['kind']} | {e['title']}")
        if d.get("pages"):
            lines.append("- Pages : " + ", ".join(f"[[{t}]]" for t in d["pages"]))
        if d.get("created"):
            lines.append("- Nouvelles : " + ", ".join(f"[[{t}]]" for t in d["created"]))
        for t in (d.get("takeaways") or [])[:5]:
            lines.append(f"- {t}")
        for f in (d.get("fixed") or [])[:12]:
            lines.append(f"- [[{f.get('title')}]] : {f.get('reason') or ''}".rstrip(" :"))
        for m in (d.get("merged") or [])[:12]:
            lines.append(f"- Fusion : {m.get('from')} → [[{m.get('into')}]]")
        if d.get("question"):
            lines.append(f"- Question : {d['question']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

wake = threading.Event()        # a job was added
sync_wake = threading.Event()   # the wiki changed: the Obsidian copy should follow


def enabled() -> bool:
    return get_settings().wiki_enabled


def enqueue_item(item_id: str) -> None:
    """An item became ready (new, reprocessed or edited): it joins the queue, once."""
    if not enabled():
        return
    db.execute("""insert into wiki_jobs (kind, item_id) values ('ingest', %s) on conflict do nothing""", (item_id,))
    wake.set()


def enqueue(kind: str, payload: dict | None = None) -> int:
    row = db.fetchone("insert into wiki_jobs (kind, payload) values (%s, %s) returning id",
                      (kind, db.jsonb(payload or {})))
    wake.set()
    return row["id"]


def get_job(job_id: int) -> dict | None:
    return db.fetchone("select id, kind, status, error, result, created_at, finished_at from wiki_jobs where id = %s",
                       (job_id,))


def queue_status() -> dict:
    row = db.fetchone(
        """select count(*) filter (where status = 'pending')::int as pending,
                  count(*) filter (where status = 'running')::int as running,
                  count(*) filter (where status = 'error' and finished_at > now() - interval '7 days')::int as errors
           from wiki_jobs""")
    current = db.fetchone(
        """select j.kind, i.title from wiki_jobs j left join items i on i.id = j.item_id
           where j.status = 'running' order by j.started_at desc limit 1""")
    return {**row, "current": current}


_HOLDER = str(uuid.uuid4())


def acquire_lease(seconds: int = LEASE_SECONDS) -> bool:
    """One wiki worker at a time, even while two containers run side by side during a deploy."""
    row = db.fetchone(
        """insert into kb_settings (key, value) values (%(key)s, %(value)s)
           on conflict (key) do update set value = excluded.value, updated_at = now()
           where kb_settings.value->>'holder' = %(holder)s
              or (kb_settings.value->>'until')::timestamptz < now()
           returning key""",
        {"key": LEASE_KEY, "holder": _HOLDER,
         "value": db.jsonb({"holder": _HOLDER,
                            "until": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()})})
    return bool(row)


def release_lease() -> None:
    db.execute("delete from kb_settings where key = %s and value->>'holder' = %s", (LEASE_KEY, _HOLDER))


def run_once() -> bool:
    """Works the next job, if any. True if there was one."""
    job = db.fetchone("select * from claim_next_wiki_job()")
    if not job:
        return False
    started = time.monotonic()
    try:
        result = run_job(job)
    except Exception as exc:
        log.warning("Wiki : échec du job %s (%s) : %s", job["id"], job["kind"], exc, exc_info=True)
        _fail(job, exc)
        return True
    db.execute("""update wiki_jobs set status = 'done', error = null, result = %s, finished_at = now()
                  where id = %s and status = 'running'""", (db.jsonb(result or {}), job["id"]))
    log.info("Wiki : job %s (%s) fait en %.1fs", job["id"], job["kind"], time.monotonic() - started)
    sync_wake.set()
    return True


def run_job(job: dict) -> dict:
    if job["kind"] == "ingest":
        return ingest_item(str(job["item_id"])) if job["item_id"] else {"skipped": "élément supprimé"}
    if job["kind"] == "answer":
        return file_answer(job["payload"] or {})
    if job["kind"] == "lint":
        return lint()
    raise ValueError(f"Job inconnu : {job['kind']}")


def _fail(job: dict, exc: Exception) -> None:
    message = f"{type(exc).__name__}: {exc}"[:1000]
    try:
        if job["attempts"] >= MAX_ATTEMPTS:
            db.execute("update wiki_jobs set status = 'error', error = %s, finished_at = now() where id = %s",
                       (message, job["id"]))
            return
        if job.get("item_id") and db.fetchone(
                "select 1 from wiki_jobs where item_id = %s and kind = 'ingest' and status = 'pending' and id <> %s",
                (job["item_id"], job["id"])):
            # the item was queued again while this ran (reprocessed, edited): the newer job does the work
            db.execute("""update wiki_jobs set status = 'done', error = %s, result = '{"superseded": true}',
                          finished_at = now() where id = %s""", (message, job["id"]))
            return
        retry_at = datetime.now(timezone.utc) + timedelta(
            seconds=10 if isinstance(exc, Conflict) else 120 * job["attempts"])
        db.execute("update wiki_jobs set status = 'pending', error = %s, next_attempt_at = %s where id = %s",
                   (message, retry_at, job["id"]))
    except Exception:  # noqa: BLE001 — never leave the worker on a failure to record a failure
        log.exception("Wiki : échec du job %s non enregistré", job["id"])


def drain() -> int:
    """Every job now, one after the other (tests, and « Tout traiter » when no worker runs here)."""
    n = 0
    while run_once():
        n += 1
    return n


# ---------------------------------------------------------------------------
# Ingest: an item into the pages it concerns
# ---------------------------------------------------------------------------

def source_text(item: dict) -> str:
    lines = [f"Référence : [src:{ref(item['id'])}]", f"Infos : {item_meta_line(item)}",
             f"Titre : {item.get('title') or '(sans titre)'}"]
    if item.get("source_url"):
        lines.append(f"URL : {item['source_url']}")
    if item.get("user_note"):
        lines.append(f"Pourquoi l'utilisateur l'a gardé : {item['user_note']}")
    if item.get("summary"):
        lines.append(f"Résumé : {item['summary']}")
    if item.get("key_points"):
        lines.append("Points clés :\n" + "\n".join(f"- {p}" for p in item["key_points"]))
    body = (item.get("content") or item.get("input_text") or "").strip()
    if body:
        if len(body) > MAX_SOURCE_CHARS:
            body = body[:MAX_SOURCE_CHARS] + "\n[… tronqué]"
        lines.append(f"Contenu :\n{body}")
    return "\n".join(lines)


def _plan(plan: dict, pages: list[dict], limit: int) -> list[dict]:
    """The planned pages, each resolved to an existing page (by its title) or a new one, once each."""
    by_lower = {p["title"].lower(): p for p in pages}
    out, seen = [], set()
    for p in plan.get("pages") or []:
        if not isinstance(p, dict) or not str(p.get("title") or "").strip():
            continue
        title = clean_title(p["title"])
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        existing = by_lower.get(title.lower())
        out.append({"title": existing["title"] if existing else title,
                    "section": p.get("section") if p.get("section") in SECTIONS else (existing or {}).get("section", "research"),
                    "kind": p.get("kind") if p.get("kind") in KINDS else (existing or {}).get("kind", "theme"),
                    "why": str(p.get("why") or ""), "existing": existing})
    return out[:limit]


def ingest_item(item_id: str) -> dict:
    from .search import ITEM_FIELDS

    item = db.fetchone(f"select {ITEM_FIELDS}, content, input_text, status from items where id = %s", (item_id,))
    if not item or item["status"] != "ready":
        return {"skipped": "élément absent ou pas prêt"}
    s = get_settings()
    pages = all_pages()
    schema = schema_text()
    src = source_text(item)
    plan = llm.wiki_plan(source=src, index=index_text(pages), schema_text=schema, max_pages=s.wiki_max_pages_per_ingest)
    planned = _plan(plan, pages, s.wiki_max_pages_per_ingest)
    takeaways = [t for t in llm._text_list(plan.get("takeaways"))][:5]
    title = item.get("title") or "(sans titre)"
    if not planned:
        add_log("ingest", title, {"item_id": item_id, "pages": [], "takeaways": takeaways, "nothing": True})
        return {"pages": [], "created": []}

    full = {p["id"]: p for p in db.fetchall(
        f"select {PAGE_FIELDS} from wiki_pages where id = any(%s::uuid[])",
        ([p["existing"]["id"] for p in planned if p["existing"]],))}
    for p in planned:
        p["content"] = full[p["existing"]["id"]]["content"] if p["existing"] else ""
    out = llm.wiki_write(source=src, ref=ref(item_id), pages=planned,
                         titles=[p["title"] for p in pages] + [p["title"] for p in planned if not p["existing"]],
                         schema_text=schema)
    written = {clean_title(w.get("title")).lower(): w for w in out.get("pages") or []
               if isinstance(w, dict) and str(w.get("content") or "").strip()}
    saved, created = [], []
    with db.conn() as c, c.transaction():
        for p in planned:
            w = written.get(p["title"].lower())
            if not w:
                continue
            current = full.get(p["existing"]["id"]) if p["existing"] else None
            if current is None and by_title(p["title"], c):
                raise Conflict(f"La page « {p['title']} » vient d'être créée ailleurs")
            row = save_page(c, page=current, title=p["title"], section=w.get("section") or p["section"],
                            kind=current["kind"] if current else (w.get("kind") or p["kind"]),
                            summary=w.get("summary"), content=w["content"],
                            author="claude", reason="ingest", add_sources=(item_id,))
            saved.append(row["title"])
            if current is None:
                created.append(row["title"])
        add_log("ingest", title, {"item_id": item_id, "pages": saved, "created": created, "takeaways": takeaways}, c)
    return {"pages": saved, "created": created}


# ---------------------------------------------------------------------------
# Answers worth keeping
# ---------------------------------------------------------------------------

def file_answer(payload: dict) -> dict:
    question = str(payload.get("question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        raise ValueError("Réponse vide")
    sources = {int(s["n"]): s for s in payload.get("sources") or [] if isinstance(s, dict) and str(s.get("n", "")).isdigit()}
    pages = all_pages()
    meta = llm.wiki_file_answer(question=question, answer=answer, index=index_text(pages))

    def cite(m: re.Match) -> str:
        s = sources.get(int(m.group(1)))
        if not s:
            return ""
        if s.get("kind") == "wiki":
            return f"[[{s.get('title')}]]"
        return f"[src:{ref(s['id'])}]" if s.get("id") else ""

    body = re.sub(r"\[(\d{1,2})\](?!\()", cite, answer)
    content = (f"*Question : {question}*\n\n{body}" if question else body)
    title = clean_title(meta.get("title") or question[:80] or "Réponse")
    taken = {p["title"].lower() for p in pages}
    base, n = title, 2
    while title.lower() in taken:
        title, n = f"{base} ({n})", n + 1
    item_ids = [s["id"] for s in sources.values() if s.get("kind") != "wiki" and s.get("id")]
    with db.conn() as c, c.transaction():
        row = save_page(c, page=None, title=title, section=meta.get("section") or "research", kind="answer",
                        summary=meta.get("summary"), content=content, author="claude", reason="answer",
                        add_sources=tuple(item_ids))
        add_log("answer", row["title"], {"pages": [row["title"]], "question": question[:300]}, c)
    return {"slug": row["slug"], "title": row["title"]}


# ---------------------------------------------------------------------------
# The nightly review (lint)
# ---------------------------------------------------------------------------

def findings(pages: list[dict]) -> dict[str, list]:
    """What can be counted rather than judged: broken links, orphans, pages without sources, user edits that replaced
    an update of Claude's."""
    titles = {p["title"].lower(): p["title"] for p in pages}
    incoming: dict[str, int] = {t: 0 for t in titles}
    broken = []
    for p in pages:
        for target in p.get("links") or []:
            if target in incoming and target != p["title"].lower():
                incoming[target] += 1
            elif target not in titles:
                broken.append({"page": p["title"], "missing": target})
    orphans = [titles[t] for t, n in incoming.items() if n == 0]
    no_sources = [p["title"] for p in pages if not p.get("n_sources") and p["kind"] != "answer"]
    lost = [p["title"] for p in pages if (db.loads(p.get("metadata")) or {}).get("lost_update")]
    return {"broken_links": broken, "orphans": orphans, "no_sources": no_sources, "lost_updates": lost}


def _findings_text(f: dict) -> str:
    lines = []
    if f["broken_links"]:
        lines.append("Liens vers des pages inexistantes : " + "; ".join(
            f"[[{b['missing']}]] dans « {b['page']} »" for b in f["broken_links"][:40]))
    if f["orphans"]:
        lines.append("Pages orphelines (aucun lien entrant) : " + ", ".join(f["orphans"][:60]))
    if f["no_sources"]:
        lines.append("Pages sans source : " + ", ".join(f["no_sources"][:60]))
    if f["lost_updates"]:
        lines.append("Pages modifiées par l'utilisateur par-dessus une mise à jour de Claude : "
                     + ", ".join(f["lost_updates"]))
    return "\n".join(lines)


def _batches(pages: list[dict]) -> list[list[dict]]:
    """Pages grouped by section, in batches small enough for one call."""
    out, current, size = [], [], 0
    for key in SECTIONS:
        for p in (p for p in pages if p["section"] == key):
            n = len(p["content"]) + 300
            if current and size + n > LINT_BATCH_CHARS:
                out.append(current)
                current, size = [], 0
            current.append(p)
            size += n
    if current:
        out.append(current)
    return out


def _lost_update_text(p: dict) -> str | None:
    lost = (db.loads(p.get("metadata")) or {}).get("lost_update")
    if not lost:
        return None
    row = db.fetchone("select content from wiki_revisions where page_id = %s and revision = %s",
                      (p["id"], lost.get("revision")))
    return row["content"] if row else None


def _sources_text(pages: list[dict]) -> str:
    rows = db.fetchall(
        """select distinct i.id::text, i.title, i.kind, i.published_at, i.created_at from wiki_page_sources s
           join items i on i.id = s.item_id where s.page_id = any(%s::uuid[])
           order by i.created_at""", ([p["id"] for p in pages],))
    return "\n".join(f"[src:{ref(r['id'])}] {r['title'] or '(sans titre)'} · {r['kind'] or ''} · "
                     f"publié {str(r['published_at'] or '')[:10] or '?'} · sauvé {str(r['created_at'])[:10]}"
                     for r in rows)


def lint() -> dict:
    """The review. Each batch of pages goes to Claude as it is now; what comes back is applied only to those pages, at
    the revision Claude read (a page changed meanwhile, by an ingest, Obsidian or the connector, keeps its new version),
    and new pages are created. A page of another batch is never rewritten from this one, and a merge needs both pages
    in the batch and the page that stays rewritten."""
    pages = all_pages(with_content=True)
    if not pages:
        return {"fixed": [], "created": [], "merged": [], "skipped": [], "suggestions": []}
    found = findings(pages)
    schema = schema_text()
    titles = [p["title"] for p in pages]
    everywhere = {p["title"].lower() for p in pages}
    fixed, created, merged, skipped, suggestions = [], [], [], [], []
    for batch in _batches(pages):
        if not acquire_lease():
            # a long review keeps the wiki's lease between batches. Lost (another container works the wiki now): it
            # stops, records nothing as reviewed, and the job is tried again
            raise RuntimeError("Relecture interrompue : le wiki est traité ailleurs, elle reprendra")
        for p in batch:
            p["lost_update"] = _lost_update_text(p)
        read = {p["title"].lower(): p for p in batch}
        out = llm.wiki_lint(pages=batch, findings=_findings_text(found), titles=titles,
                            sources=_sources_text(batch), schema_text=schema)
        suggestions += [s for s in out.get("suggestions") or [] if isinstance(s, dict) and str(s.get("text") or "").strip()]
        returned: set[str] = set()
        with db.conn() as c, c.transaction():
            for w in out.get("pages") or []:
                if not isinstance(w, dict) or not str(w.get("content") or "").strip():
                    continue
                key = clean_title(w.get("title")).lower()
                current = read.get(key)
                if current is None and key in everywhere:
                    continue            # a page of another batch: Claude didn't read it here
                try:
                    with c.transaction():
                        row = save_page(
                            c, page=current, title=current["title"] if current else w["title"],
                            section=current["section"] if current else (w.get("section") or "research"),
                            kind=current["kind"] if current else (w.get("kind") or "theme"),
                            summary=w.get("summary"), content=w["content"], author="claude", reason="lint",
                            metadata={"lost_update": None} if current else None)
                except Conflict as exc:
                    skipped.append({"title": w.get("title"), "reason": str(exc)})
                    continue
                returned.add(key)
                (fixed if current else created).append({"title": row["title"], "reason": str(w.get("reason") or "")})
            for m in out.get("merges") or []:
                if not isinstance(m, dict):
                    continue
                src_key, dst_key = clean_title(m.get("from")).lower(), clean_title(m.get("into")).lower()
                if src_key == dst_key or src_key not in read or dst_key not in read or dst_key not in returned:
                    continue
                src, dst = read[src_key], by_title(m.get("into") or "", c)
                if not dst:
                    continue
                try:
                    with c.transaction():
                        archive_page(c, src, reason="lint: fusion", merged_into=dst)
                except Conflict as exc:
                    skipped.append({"title": src["title"], "reason": str(exc)})
                    continue
                merged.append({"from": src["title"], "into": dst["title"], "reason": str(m.get("reason") or "")})
    items = [{"text": str(s["text"]).strip(), "kind": s.get("kind") if s.get("kind") in
              ("question", "source", "action", "link") else "question"} for s in suggestions][:8]
    db.execute("""insert into kb_settings (key, value) values (%s, %s)
                  on conflict (key) do update set value = excluded.value, updated_at = now()""",
               (SUGGESTIONS_KEY, db.jsonb({"at": datetime.now(timezone.utc).isoformat(), "items": items})))
    n = len(fixed) + len(created) + len(merged)
    add_log("lint", f"Relecture : {n} correction{'s' if n > 1 else ''}" if n else "Relecture : rien à corriger",
            {"fixed": fixed, "created": [c["title"] for c in created], "merged": merged, "skipped": skipped,
             "findings": {k: len(v) for k, v in found.items()}})
    return {"fixed": fixed, "created": created, "merged": merged, "skipped": skipped, "suggestions": items}


def suggestions() -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (SUGGESTIONS_KEY,))
    return dict(row["value"]) if row else {"at": None, "items": []}


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().digest_timezone))


def lint_due(now: datetime | None = None) -> int | None:
    """Once a night, after WIKI_LINT_HOUR: queues the review if the wiki changed since the last one."""
    s = get_settings()
    now = (now or _local_now()).astimezone(ZoneInfo(s.digest_timezone))
    if now.hour < s.wiki_lint_hour:
        return None
    today = now.date().isoformat()
    claimed = db.fetchone(
        """insert into kb_settings (key, value) values (%(key)s, %(value)s)
           on conflict (key) do update set value = kb_settings.value || excluded.value, updated_at = now()
           where kb_settings.value->>'day' is distinct from %(day)s
           returning key""",
        {"key": LINT_KEY, "day": today, "value": db.jsonb({"day": today})})
    if not claimed:
        return None
    changed = db.fetchone(
        """select exists (select 1 from wiki_log where kind in ('ingest', 'answer', 'edit')
                          and at > coalesce((select max(at) from wiki_log where kind = 'lint'), '-infinity')) as yes""")
    if not changed["yes"]:
        return None
    return enqueue("lint", {"reason": "nightly"})


def last_lint() -> dict | None:
    row = db.fetchone("select at, title, detail from wiki_log where kind = 'lint' order by at desc limit 1")
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# What the app shows
# ---------------------------------------------------------------------------

def overview() -> dict:
    from . import obsidian

    pages = all_pages()
    counts = {k: 0 for k in SECTIONS}
    for p in pages:
        counts[p["section"]] = counts.get(p["section"], 0) + 1
    return {
        "enabled": enabled(),
        "sections": [{"key": k, "label": fr, "label_en": en, "count": counts[k]} for k, (fr, en, _) in SECTIONS.items()],
        "pages": [{k: p[k] for k in ("id", "title", "slug", "section", "kind", "summary", "updated_at", "updated_by",
                                     "n_sources")} for p in pages],
        "queue": queue_status(),
        "last_lint": last_lint(),
        "suggestions": suggestions(),
        "obsidian": obsidian.status(),
    }


def page_detail(key: str) -> dict | None:
    page = get_page(key)
    if not page:
        return None
    page = dict(page)
    from .formatting import source_card

    sources = page_sources(page["id"])
    refs = {ref(s["id"]): {"id": s["id"], "title": s.get("title")} for s in sources}
    missing = [r for r in cites_of(page["content"]) if r not in refs]
    for r, it in resolve_refs(missing).items():
        refs[r] = {"id": it["id"], "title": it.get("title")}
    history = db.fetchall(
        """select revision, author, reason, created_at from wiki_revisions where page_id = %s
           order by revision desc limit 20""", (page["id"],))
    page.update(
        sources=[source_card(n, s) for n, s in enumerate(sources, 1)],
        refs=refs,
        links_resolved=resolve_titles(page["links"]),
        backlinks=backlinks(page["title"]),
        history=history,
        merged_into=None,
    )
    meta = db.loads(page.get("metadata")) or {}
    if page["archived"] and meta.get("merged_into"):
        target = db.fetchone("select slug, title from wiki_pages where id = %s", (meta["merged_into"],))
        page["merged_into"] = target
    return page


def search_pages(query: str, limit: int = 10) -> list[dict]:
    return db.fetchall(
        """select id::text, title, slug, section, kind, summary, ts_rank_cd(fts, q) as rank
           from wiki_pages, kb_or_query(%s) q where not archived and q is not null and fts @@ q
           order by rank desc limit %s""", (query, max(1, min(limit, 30))))


def write_from_chat(*, title: str, content: str, section: str | None = None, summary: str | None = None,
                    revision: int | None = None) -> dict:
    """A page written or rewritten from a conversation in Claude (connector): like any write, one revision more.
    `revision`: the one Claude read; if the page changed since, nothing is written (Conflict)."""
    with db.conn() as c, c.transaction():
        current = by_title(title, c)
        if current and revision is not None and current["revision"] != revision:
            raise Conflict(f"La page « {current['title']} » a changé depuis ta lecture (révision {current['revision']}, "
                           f"tu avais lu la {revision}) : relis-la avant de la réécrire")
        row = save_page(c, page=current, title=title, section=section or (current or {}).get("section") or "research",
                        kind=(current or {}).get("kind") or "theme", summary=summary or (current or {}).get("summary"),
                        content=content, author="claude", reason="chat")
        add_log("edit", row["title"], {"pages": [row["title"]], "source": "chat"}, c)
    sync_wake.set()
    return row


BACKFILL_KEY = "wiki_backfilled"


def backfill_once() -> int:
    """The items that were ready before the wiki existed join its queue, once (a flag in kb_settings), oldest first."""
    if not enabled():
        return 0
    with db.conn() as c, c.transaction():
        claimed = c.execute("""insert into kb_settings (key, value) values (%s, %s) on conflict (key) do nothing
                               returning key""", (BACKFILL_KEY, db.jsonb({"at": datetime.now(timezone.utc).isoformat()})))
        if not claimed.fetchone():
            return 0
        rows = c.execute(
            """insert into wiki_jobs (kind, item_id, created_at)
               select 'ingest', i.id, i.created_at from items i
               where i.status = 'ready'
                 and not exists (select 1 from wiki_jobs j where j.item_id = i.id and j.kind = 'ingest')
               order by i.created_at
               on conflict do nothing returning id""").fetchall()
    if rows:
        log.info("Wiki : %d éléments d'avant mis en file", len(rows))
        wake.set()
    return len(rows)


def _wake_sync() -> None:
    sync_wake.set()
    wake.set()


# ---------------------------------------------------------------------------
# The worker thread: the queue, the nightly review, the Obsidian sync
# ---------------------------------------------------------------------------

_worker: WikiWorker | None = None


def worker_alive() -> bool:
    return bool(_worker and _worker._thread and _worker._thread.is_alive())


class WikiWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        global _worker
        _worker = self
        self._thread = threading.Thread(target=self._loop, name="kb-wiki", daemon=True)
        self._thread.start()
        log.info("Wiki activé : intégration au fil de l'eau, relecture chaque nuit à %d h", get_settings().wiki_lint_hour)

    def stop(self) -> None:
        self._stop.set()
        wake.set()
        try:
            release_lease()
        except Exception:  # noqa: BLE001 — the lease expires anyway
            pass

    def _loop(self) -> None:
        from . import obsidian

        self._stop.wait(8)          # the API and the item worker first
        try:
            backfill_once()
        except Exception:  # noqa: BLE001 — tried again at the next start
            log.exception("Wiki : mise en file des éléments d'avant impossible")
        last_sync = 0.0
        held_until = 0.0            # the lease is renewed when it gets close to expiring, not at every turn
        while not self._stop.is_set():
            did = False
            try:
                if time.monotonic() > held_until - LEASE_SECONDS / 4:
                    held_until = time.monotonic() + LEASE_SECONDS if acquire_lease() else 0.0
                if held_until:
                    lint_due()
                    did = run_once()
                    interval = get_settings().wiki_git_interval
                    idle = not did
                    if obsidian.enabled() and ((sync_wake.is_set() and idle) or time.monotonic() - last_sync > interval):
                        sync_wake.clear()
                        last_sync = time.monotonic()
                        obsidian.sync_safely()
            except Exception:
                log.exception("Wiki : erreur du worker")
            if not did:
                wake.wait(timeout=10)
                wake.clear()


def today() -> date:
    return _local_now().date()
