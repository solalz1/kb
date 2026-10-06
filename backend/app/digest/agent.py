"""The tech-digest agent: every morning a digest of the last 24 hours, every Monday a digest of the week with project
ideas, both ordered from the most general to the most technical and tuned to what the user saves in the KB.

Flow: collect candidates (sources.py) → Claude Haiku picks and files the relevant ones → Claude Sonnet (Opus for the
week) writes the entries → stored in `digests`, shown in the app, optionally e-mailed. Every URL comes from a real
source: the model only refers to candidates by id.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import db, llm, pipeline
from ..config import get_settings
from . import profile, render, sources

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=45)       # a "generating" digest older than this was interrupted
RETRY_AFTER = timedelta(hours=1)
MAX_ATTEMPTS = 3
MAX_CANDIDATES = 220
PROJECT_KINDS = ["benchmark", "reproduction", "outil", "agent", "analyse", "contribution", "ecriture"]

_threads_lock = threading.Lock()
_running: set[int] = set()


# ---------------------------------------------------------------------------
# Prompts and schemas
# ---------------------------------------------------------------------------

def _sections_help() -> str:
    return "\n".join(f"- {sid} ({label}) : {desc}" for sid, label, desc in render.SECTIONS)


PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "section": {"type": "string", "enum": render.SECTION_IDS},
                    "also": {"type": "array", "items": {"type": "string"},
                             "description": "Autres ids qui couvrent la même histoire."},
                },
                "required": ["id", "section"],
            },
        }
    },
    "required": ["picks"],
}

ENTRY = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string", "description": "Titre court et informatif ; noms propres d'origine."},
        "summary": {"type": "string", "description": "2 à 3 phrases factuelles : ce qui est nouveau, chiffres, noms."},
        "why": {"type": "string", "description": "Facultatif : une phrase qui relie l'élément à ses sujets ou projets."},
    },
    "required": ["id", "title", "summary"],
}

PROJECT = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "pitch": {"type": "string", "description": "Deux phrases : ce qu'il construit et ce que ça montre."},
        "why_now": {"type": "string", "description": "Le lien avec l'actualité de la semaine ou ses objectifs."},
        "refs": {"type": "array", "items": {"type": "string"}, "description": "Ids des éléments qui l'inspirent."},
        "learn": {"type": "string", "description": "Ce qu'il apprend ou démontre."},
        "plan": {"type": "array", "items": {"type": "string"}, "description": "4 à 6 étapes concrètes, avec une durée."},
        "deliverable": {"type": "string", "description": "Ce qu'il publie à la fin : dépôt, figure, article, démo."},
        "effort": {"type": "string", "description": "Temps total estimé, ex. « ≈ 6 h »."},
        "difficulty": {"type": "integer", "enum": [1, 2, 3]},
        "kind": {"type": "string", "enum": PROJECT_KINDS},
    },
    "required": ["title", "pitch", "why_now", "refs", "learn", "plan", "deliverable", "effort", "difficulty", "kind"],
}

DAILY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Une phrase qui résume la journée."},
        "entries": {"type": "array", "items": ENTRY},
    },
    "required": ["headline", "entries"],
}

WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Une phrase qui résume la semaine."},
        "trends": {
            "type": "array",
            "description": "3 à 6 tendances ou faits marquants de la semaine.",
            "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}},
                "required": ["text", "refs"]},
        },
        "entries": {"type": "array", "items": {**ENTRY, "properties": {
            **ENTRY["properties"], "section": {"type": "string", "enum": render.SECTION_IDS}}},
            "description": "Les 10 à 16 éléments de la semaine à retenir."},
        "projects": {"type": "array", "items": PROJECT, "description": "4 ou 5 projets pour cette semaine."},
    },
    "required": ["headline", "trends", "entries", "projects"],
}

PROJECTS_SCHEMA = {
    "type": "object",
    "properties": {"projects": {"type": "array", "items": PROJECT}},
    "required": ["projects"],
}

PROJECT_RULES = """Projets :
- Faisables en une semaine à côté d'un emploi (4 à 10 h au total), avec un résultat public à la fin : un dépôt
  GitHub, une figure, un court article ou un thread. Ils doivent montrer un vrai savoir-faire d'ingénieur ML.
- Le genre de projet qu'il aime : un petit benchmark ou une éval originale née d'une discussion du moment (ex. poser
  à des LLM une question simple et mesurable, sur beaucoup de cas, et tracer le résultat), reproduire un résultat de
  papier à petite échelle, construire un outil ou un agent qui lui sert, analyser un phénomène avec des données.
- Varie les types. Ancre chaque projet dans un élément précis (refs) ou dans ses objectifs.
- Concret : quels modèles ou API, quelles données, quel coût approximatif, quelle figure finale.
- Rien de déjà proposé ni de déjà fait (voir la liste)."""


def _candidate_line(c: dict) -> str:
    bits = [c["source"], c["kind"]]
    if c.get("score"):
        bits.append(f"★{int(c['score'])}")
    who = f" — {c['author']}" if c.get("author") else ""
    followed = f" — suivi : {c['person']}" if c.get("person") else ""
    return f"[{c['key']}] ({' · '.join(bits)}) {c['title']}{who}{followed}\n    {c.get('text', '')[:220]}"


def _trim(cands: list[dict], limit: int = MAX_CANDIDATES) -> list[dict]:
    """Keep every post from followed people, then the most popular items of each source in turn."""
    followed = [c for c in cands if c.get("person")]
    by_source: dict[str, list[dict]] = defaultdict(list)
    for c in sorted((c for c in cands if not c.get("person")), key=lambda c: c.get("score") or 0, reverse=True):
        by_source[c["source"]].append(c)
    out = followed[: limit // 2]
    while len(out) < limit and any(by_source.values()):
        for src in list(by_source):
            if by_source[src]:
                out.append(by_source[src].pop(0))
    return out[:limit]


# ---------------------------------------------------------------------------
# Building digests
# ---------------------------------------------------------------------------

_SECTION_ALIASES = {
    **{sid: sid for sid in render.SECTION_IDS},
    **{unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower(): sid
       for sid, label, _ in render.SECTIONS},
    "modele": "modeles", "models": "modeles", "industry": "industrie", "research": "recherche",
    "engineering": "ingenierie", "voices": "voix", "essential": "essentiel",
}


def section_id(value) -> str | None:
    """The model sometimes writes « ingénierie » or « Modèles et labs » instead of the id: map it back."""
    key = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower().strip()
    return _SECTION_ALIASES.get(key) or _SECTION_ALIASES.get(key.split(" ")[0])


def _entry(c: dict, section: str, written: dict, in_kb: set[str]) -> dict:
    return {
        "key": c["key"], "section": section, "title": written.get("title") or c["title"],
        "summary": written.get("summary") or "", "why": written.get("why") or "", "url": c["url"],
        "source": c["source"], "author": c.get("author"), "person": c.get("person"), "kind": c["kind"],
        "published_at": c.get("published_at"), "in_kb": c["url"] in in_kb,
        "links": c.get("links") or {k: v for k, v in (c.get("extra") or {}).items() if k in ("discussion", "arxiv") and v},
    }


def _profile_text() -> str:
    try:
        return profile.as_text(profile.compute())
    except Exception:
        log.warning("Profil d'intérêts indisponible", exc_info=True)
        return profile.as_text(profile.get()) or "Ingénieur IA : culture tech générale, LLM et ML."


def build_daily(day: date) -> dict:
    s = get_settings()
    prof = _profile_text()
    since = datetime.now(timezone.utc) - timedelta(hours=26)
    seen = sources.shown_recently(except_day=day)
    cands = [c.as_dict() for c in sources.collect(since) if c.key not in seen]
    if not cands:
        return {"headline": "Rien de neuf dans tes sources depuis hier.", "data": {"entries": []}, "model": None}
    cands = _trim(cands)
    by_key = {c["key"]: c for c in cands}

    picked = llm.call_tool(
        system=(
            f"Tu es l'agent de veille tech de l'utilisateur. Chaque matin, tu choisis parmi les nouvelles des dernières "
            f"24 h celles qui entrent dans son digest de culture générale tech, rangées du plus général au plus "
            f"technique.\n\nSon profil :\n{prof}\n\nSections :\n{_sections_help()}\n\nRègles :\n"
            f"- 15 à 25 éléments au total ; 2 à 4 dans « essentiel », même hors de ses sujets.\n"
            f"- « voix » : les publications des personnes qu'il suit (marquées « suivi ») qui ont du fond.\n"
            f"- Ailleurs, privilégie ses sujets et son niveau ; écarte ce qu'il veut éviter et le contenu promotionnel.\n"
            f"- Une même histoire vue dans plusieurs sources : une seule entrée, les autres ids dans also."
        ),
        content="Candidats :\n" + "\n".join(_candidate_line(c) for c in cands),
        tool_name="pick_items", tool_description="Enregistre les éléments retenus et leur section.",
        schema=PICK_SCHEMA, max_tokens=3000,
    )
    picks = []
    for p in picked.get("picks") or []:
        sid = section_id(p.get("section"))
        if p.get("id") in by_key and sid and p["id"] not in {x["id"] for x in picks}:
            picks.append({**p, "section": sid})
    picks = picks[:30]
    if not picks:
        return {"headline": "Rien de marquant pour toi aujourd'hui.", "data": {"entries": []}, "model": None}

    details = "\n\n".join(
        f"[{p['id']}] section {p['section']} — {by_key[p['id']]['source']} — {by_key[p['id']]['title']}"
        + (f" — {by_key[p['id']]['author']}" if by_key[p["id"]].get("author") else "")
        + f"\n{by_key[p['id']].get('text', '')[:1200]}"
        + "".join(f"\nAutre source : {by_key[a]['title']} — {by_key[a].get('text', '')[:300]}"
                  for a in (p.get("also") or [])[:2] if a in by_key)
        for p in picks
    )
    written = llm.call_tool(
        system=(
            f"Tu rédiges le digest tech quotidien de l'utilisateur, en {llm.lang_name()}, en le tutoyant.\n"
            f"Son profil :\n{prof}\n\nPour chaque élément : un titre court (garde les noms propres), un résumé factuel "
            f"de 2 à 3 phrases (ce qui est nouveau, chiffres, noms), et une phrase « why » seulement si elle relie "
            f"vraiment l'élément à ses sujets. Range les éléments de chaque section du plus important au moins "
            f"important. Commence par une phrase d'accroche (headline). N'invente rien : si la source est maigre, sois bref."
        ),
        content=f"Date : {render.french_date(day)}\n\n{details}",
        tool_name="write_digest", tool_description="Enregistre le digest rédigé.",
        schema=DAILY_SCHEMA, model=s.digest_model, max_tokens=8000,
    )
    section_of = {p["id"]: p["section"] for p in picks}
    in_kb = sources.already_in_kb([c["url"] for c in cands])
    entries, done = [], set()
    for w in written.get("entries") or []:
        if w.get("id") in section_of and w["id"] not in done:
            done.add(w["id"])
            entries.append(_entry(by_key[w["id"]], section_of[w["id"]], w, in_kb))
    entries.sort(key=lambda e: render.SECTION_IDS.index(e["section"]))
    return {"headline": written.get("headline", ""), "data": {"entries": entries}, "model": s.digest_model}


def _goals() -> list[dict]:
    return db.fetchall(
        """select title, left(coalesce(summary, input_text), 400) as summary from items
           where space = 'perso' and category = 'objectif' and not archived order by pinned desc, created_at desc limit 8""")


def _project_context(since: date) -> str:
    saved = db.fetchall(
        """select title from items where status = 'ready' and space = 'main' and created_at >= %s and title is not null
           order by created_at desc limit 30""", (since,))
    past = db.fetchall(
        """select p->>'title' as title, f.vote
           from digests d cross join lateral jsonb_array_elements(coalesce(d.data->'projects', '[]')) p
           left join digest_feedback f on f.digest_id = d.id and f.target = 'project' and f.entry_key = p->>'key'
           where d.created_at > now() - interval '60 days' order by d.created_at desc limit 40""")
    projects_kb = db.fetchall(
        """select title from items where 'projet' = any(tags) and status = 'ready' order by created_at desc limit 20""")
    verdict = {2: "il l'a fait", 1: "intéressé", -1: "pas pour lui"}
    goals = _goals()
    return "\n\n".join([
        "Ses objectifs (espace Perso) :\n" + ("\n".join(f"- {g['title']} : {g['summary']}" for g in goals) or "(aucun)"),
        "Ce qu'il a sauvegardé récemment :\n" + ("\n".join(f"- {r['title']}" for r in saved) or "(rien)"),
        "Projets déjà proposés :\n" + ("\n".join(
            f"- {r['title']}" + (f" ({verdict[r['vote']]})" if r["vote"] in verdict else "") for r in past) or "(aucun)"),
        "Projets déjà dans sa KB :\n" + ("\n".join(f"- {r['title']}" for r in projects_kb) or "(aucun)"),
    ])


def _project(p: dict) -> dict:
    p = {k: p.get(k) for k in PROJECT["properties"]}
    p["key"] = hashlib.sha1((p.get("title") or "").encode()).hexdigest()[:10]
    p["plan"] = [x for x in p.get("plan") or [] if x][:8]
    return p


def _week_material(start: date, end: date) -> tuple[list[dict], list[dict]]:
    """Entries of the week's daily digests (with the user's votes), then fresh candidates not seen in them."""
    rows = db.fetchall(
        """select d.id, e from digests d, jsonb_array_elements(coalesce(d.data->'entries', '[]')) e
           where d.kind = 'daily' and d.status = 'ready' and d.period_start between %s and %s""", (start, end))
    votes = {(r["digest_id"], r["entry_key"]): r["vote"] for r in db.fetchall(
        "select digest_id, entry_key, vote from digest_feedback where target = 'entry' and created_at > %s",
        (start,))}
    dailies = []
    for r in rows:
        e = dict(r["e"])
        e["vote"] = votes.get((r["id"], e["key"]))
        dailies.append(e)
    known = {e["key"] for e in dailies}
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    fresh = [c.as_dict() for c in sources.collect(since, weekly=True) if c.key not in known]
    return dailies, _trim(fresh, 90)


def build_weekly(start: date, end: date) -> dict:
    s = get_settings()
    try:
        prof = profile.as_text(profile.compute(force=True))
    except Exception:
        log.warning("Profil d'intérêts non recalculé", exc_info=True)
        prof = _profile_text()
    dailies, fresh = _week_material(start, end)
    by_key = {e["key"]: {**e, "text": e.get("summary", "")} for e in dailies}
    by_key.update({c["key"]: c for c in fresh})
    if not by_key:
        return {"headline": "Semaine calme dans tes sources.", "data": {"entries": [], "trends": [], "projects": []},
                "model": None}
    vote_label = {1: " [il a aimé]", 2: " [il l'a gardé]", -1: " [pas pour lui]"}
    material = "\n".join(
        f"[{e['key']}] ({e['section']} · {e['source']}) {e['title']}{vote_label.get(e.get('vote'), '')}\n    {e.get('summary', '')[:300]}"
        for e in dailies)
    out = llm.call_tool(
        system=(
            f"Tu rédiges le digest hebdomadaire de l'utilisateur, en {llm.lang_name()}, en le tutoyant : la culture "
            f"générale tech de la semaine du plus général au plus technique, puis des projets à entreprendre cette "
            f"semaine.\n\nSon profil :\n{prof}\n\nSections :\n{_sections_help()}\n\n"
            f"Retiens 10 à 16 éléments (pas seulement ceux qu'il a aimés), écris 3 à 6 tendances qui relient plusieurs "
            f"éléments (refs), et propose 4 ou 5 projets.\n\n{PROJECT_RULES}\n\nN'utilise que les ids fournis."
        ),
        content=(f"Semaine du {render.french_date(start)} au {render.french_date(end)}\n\n"
                 f"Déjà dans ses digests quotidiens :\n{material or '(aucun digest quotidien)'}\n\n"
                 f"Autres éléments de la semaine :\n" + ("\n".join(_candidate_line(c) for c in fresh) or "(aucun)")
                 + "\n\n" + _project_context(start)),
        tool_name="write_weekly", tool_description="Enregistre le digest de la semaine et les projets.",
        schema=WEEKLY_SCHEMA, model=s.digest_weekly_model, max_tokens=12000,
    )
    in_kb = sources.already_in_kb([c["url"] for c in by_key.values()])
    entries = []
    for w in out.get("entries") or []:
        c, sid = by_key.get(w.get("id")), section_id(w.get("section"))
        if c and sid and w["id"] not in {e["key"] for e in entries}:
            entries.append(_entry(c, sid, w, in_kb))
    entries.sort(key=lambda e: render.SECTION_IDS.index(e["section"]))
    known = {e["key"] for e in entries}
    # projects and trends may cite items that didn't make the final list: keep those as hidden references
    refs = [_entry(by_key[r], section_id(by_key[r].get("section")) or "ingenierie", {}, in_kb) | {"hidden": True}
            for r in {r for x in (out.get("projects") or []) + (out.get("trends") or []) for r in x.get("refs") or []}
            if r in by_key and r not in known]
    trends = [{"text": t["text"], "refs": [r for r in t.get("refs") or [] if r in by_key]} for t in out.get("trends") or []]
    projects = [_project(p) for p in out.get("projects") or []]
    for p in projects:
        p["refs"] = [r for r in p.get("refs") or [] if r in by_key]
    return {"headline": out.get("headline", ""), "model": s.digest_weekly_model,
            "data": {"entries": entries + refs, "trends": trends, "projects": projects}}


def _visible(entries: list[dict]) -> list[dict]:
    return [e for e in entries if not e.get("hidden")]


# ---------------------------------------------------------------------------
# Storage, scheduling
# ---------------------------------------------------------------------------

def get(digest_id: int) -> dict | None:
    return db.fetchone("select * from digests where id = %s", (digest_id,))


def _store(digest_id: int, built: dict) -> dict:
    row = get(digest_id)
    row.update({"headline": built["headline"], "data": built["data"]})
    content = render.markdown({**row, "data": {**built["data"], "entries": _visible(built["data"].get("entries", []))}})
    return db.fetchone(
        """update digests set status = 'ready', headline = %s, content = %s, data = %s, model = %s, error = null,
                              updated_at = now() where id = %s returning *""",
        (built["headline"], content, db.jsonb(built["data"]), built.get("model"), digest_id),
    )


def generate(digest_id: int) -> dict | None:
    """Build (or rebuild) a digest row, then e-mail it if configured."""
    with _threads_lock:
        if digest_id in _running:
            return None
        _running.add(digest_id)
    try:
        row = get(digest_id)
        if row["kind"] == "weekly":
            built = build_weekly(row["period_start"], row["period_end"])
        else:
            built = build_daily(row["period_start"])
        done = _store(digest_id, built)
        log.info("Digest %s prêt (%d éléments)", digest_id, len(_visible(built["data"].get("entries", []))))
        try:
            render.send_email(done)
        except Exception:
            log.warning("Digest %s non envoyé par e-mail", digest_id, exc_info=True)
        return done
    except Exception as exc:
        log.exception("Digest %s en échec", digest_id)
        db.execute("update digests set status = 'error', error = %s, updated_at = now() where id = %s",
                   (f"{type(exc).__name__}: {exc}"[:1000], digest_id))
        return None
    finally:
        with _threads_lock:
            _running.discard(digest_id)


def periods(kind: str, today: date) -> tuple[date, date]:
    """Daily: today. Weekly: the previous Monday-to-Sunday week (written on Monday, or later if the server was down)."""
    if kind == "weekly":
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday - timedelta(days=1)
    return today, today


_TAKEOVER = """(status = 'error' and attempts < %(max)s and updated_at < now() - %(retry)s)
               or (status = 'generating' and updated_at < now() - %(stale)s)"""
_NOT_RUNNING = "not (status = 'generating' and updated_at > now() - %(stale)s)"


def claim(kind: str, start: date, end: date, *, force: bool = False) -> int | None:
    """Create the digest row for this period, or take over a failed/interrupted one (any finished one if `force`).
    None if there's nothing to do, or if it's being generated right now."""
    row = db.fetchone(
        """insert into digests (kind, period_start, period_end) values (%s, %s, %s)
           on conflict (kind, period_start) do nothing returning id""", (kind, start, end))
    if row:
        return row["id"]
    params = {"kind": kind, "start": start, "max": MAX_ATTEMPTS, "retry": RETRY_AFTER, "stale": STALE_AFTER}
    if force:     # a manual rebuild starts a fresh retry budget
        sql = f"""update digests set status = 'generating', attempts = 1, error = null, updated_at = now()
                  where kind = %(kind)s and period_start = %(start)s and {_NOT_RUNNING} returning id"""
    else:
        sql = f"""update digests set status = 'generating', attempts = attempts + 1, error = null, updated_at = now()
                  where kind = %(kind)s and period_start = %(start)s and ({_TAKEOVER}) returning id"""
    row = db.fetchone(sql, params)
    return row["id"] if row else None


def local_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().digest_timezone))


def run_due(now: datetime | None = None) -> list[int]:
    """Called every minute by the scheduler, once the hour has come: the week's digest if it's missing (normally on
    Monday morning), then today's."""
    s = get_settings()
    now = (now or local_now()).astimezone(ZoneInfo(s.digest_timezone))
    if now.hour < s.digest_hour:
        return []
    done = []
    for kind in ("weekly", "daily"):
        start, end = periods(kind, now.date())
        digest_id = claim(kind, start, end)
        if digest_id:
            generate(digest_id)
            done.append(digest_id)
    return done


def start_now(kind: str) -> int:
    """« Générer maintenant » from the app: (re)build the current period's digest in the background."""
    start, end = periods(kind, local_now().date())
    digest_id = claim(kind, start, end, force=True)
    if digest_id is None:      # already being generated right now
        return db.fetchone("select id from digests where kind = %s and period_start = %s", (kind, start))["id"]
    spawn(generate, digest_id)
    return digest_id


def regenerate(digest_id: int) -> bool:
    """Rebuild this exact digest (retry after an error, or a fresh take on an archived one). False if it's running."""
    row = db.fetchone(
        f"""update digests set status = 'generating', attempts = 1, error = null, updated_at = now()
            where id = %(id)s and {_NOT_RUNNING} and not coalesce((data->>'projects_pending')::boolean, false)
            returning id""",
        {"id": digest_id, "stale": STALE_AFTER},
    )
    if row:
        spawn(generate, digest_id)
    return bool(row)


def spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="kb-digest").start()


class Scheduler:
    def __init__(self):
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="kb-digest-scheduler", daemon=True).start()
        s = get_settings()
        log.info("Digest activé : chaque jour à %d h (%s), le lundi avec la semaine", s.digest_hour, s.digest_timezone)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(20)          # let the API start first
        while not self._stop.is_set():
            try:
                run_due()
            except Exception:
                log.exception("Planificateur du digest")
            self._stop.wait(60)


# ---------------------------------------------------------------------------
# Projects on demand and feedback
# ---------------------------------------------------------------------------

def more_projects(digest_id: int, count: int = 3) -> dict | None:
    """Add project ideas to a digest, from the last 7 days of digests."""
    s = get_settings()
    row = get(digest_id)
    if not row:
        return None
    since = row["period_end"] - timedelta(days=7)
    recent = db.fetchall(
        """select e from digests d, jsonb_array_elements(coalesce(d.data->'entries', '[]')) e
           where d.status = 'ready' and d.period_end > %s and d.period_end <= %s""", (since, row["period_end"]))
    by_key = {}
    for r in recent:
        by_key.setdefault(r["e"]["key"], r["e"])
    existing = row["data"].get("projects") or []
    out = llm.call_tool(
        system=(f"Tu proposes à l'utilisateur {count} projets à entreprendre cette semaine, en {llm.lang_name()}, en le "
                f"tutoyant.\n\nSon profil :\n{_profile_text()}\n\n{PROJECT_RULES}\n\nN'utilise que les ids fournis."),
        content=("Actualité récente (ses digests) :\n" + ("\n".join(
                    f"[{k}] ({e.get('section')}) {e['title']} : {e.get('summary', '')[:250]}" for k, e in by_key.items())
                    or "(rien)")
                 + "\n\nProjets déjà proposés dans ce digest :\n" + ("\n".join(f"- {p['title']}" for p in existing) or "(aucun)")
                 + "\n\n" + _project_context(since)),
        tool_name="propose_projects", tool_description="Enregistre les projets proposés.",
        schema=PROJECTS_SCHEMA, model=s.digest_weekly_model, max_tokens=6000,
    )
    keys = {p["key"] for p in existing}
    added = [p for p in (_project(p) for p in out.get("projects") or []) if p["key"] not in keys]
    known = {e["key"] for e in row["data"].get("entries") or []}
    hidden = []
    for p in added:
        p["refs"] = [r for r in p.get("refs") or [] if r in by_key]
        for r in p["refs"]:
            if r not in known:
                hidden.append({**by_key[r], "hidden": True})
                known.add(r)
    # append in one statement: a concurrent run or a rebuild never loses or resurrects anything
    done = db.fetchone(
        """update digests set data = jsonb_set(jsonb_set(data,
                  '{projects}', coalesce(data->'projects', '[]'::jsonb) || %s),
                  '{entries}', coalesce(data->'entries', '[]'::jsonb) || %s), updated_at = now()
           where id = %s and status = 'ready' returning *""",
        (db.jsonb(added), db.jsonb(hidden), digest_id),
    )
    if done:
        visible = {**done["data"], "entries": _visible(done["data"].get("entries") or [])}
        db.execute("update digests set content = %s where id = %s", (render.markdown({**done, "data": visible}), digest_id))
    return done


def start_more_projects(digest_id: int) -> bool:
    """False if the digest isn't ready or ideas are already being looked for."""
    row = db.fetchone(
        """update digests set data = data || '{"projects_pending": true}'
           where id = %s and status = 'ready' and not coalesce((data->>'projects_pending')::boolean, false)
           returning id""", (digest_id,))
    if not row:
        return False

    def run():
        try:
            more_projects(digest_id)
        except Exception:
            log.exception("Projets supplémentaires en échec")
        finally:
            db.execute("update digests set data = data - 'projects_pending' where id = %s", (digest_id,))

    spawn(run)
    return True


def project_note(p: dict, digest: dict) -> str:
    refs = {e["key"]: e for e in digest["data"].get("entries") or []}
    body = render.project_markdown(p, refs).split("\n", 1)[1]
    return f"{body}\n\nProposé par le digest « {render.title_for(digest)} »."


def feedback(digest_id: int, target: str, key: str, vote: int) -> dict:
    """Votes: 1 interesting, -1 not for me, 0 withdrawn, 2 keep (entry → saved to the KB; project → « je le fais », saved
    as a note). Keeping twice never creates two items."""
    digest = get(digest_id)
    if not digest:
        raise LookupError("Digest introuvable")
    pool = digest["data"].get("entries" if target == "entry" else "projects") or []
    thing = next((x for x in pool if x.get("key") == key), None)
    if not thing:
        raise LookupError("Élément introuvable dans ce digest")
    lock = int(hashlib.sha1(f"{digest_id}:{target}:{key}".encode()).hexdigest()[:15], 16)
    with db.conn() as c, c.transaction():
        c.execute("select pg_advisory_xact_lock(%s)", (lock,))
        prev = c.execute("select item_id::text from digest_feedback where digest_id = %s and target = %s and entry_key = %s",
                         (digest_id, target, key)).fetchone()
        item_id = prev["item_id"] if prev else None
        if vote == 2 and not item_id:
            if target == "entry":
                item_id = pipeline.ingest(url=thing["url"], note=f"Repéré dans ton digest ({render.title_for(digest)})")["id"]
            else:
                item_id = pipeline.create_note(content=project_note(thing, digest), title=thing["title"], space="main",
                                               tags=["projet"])["id"]
        c.execute(
            """insert into digest_feedback (digest_id, target, entry_key, vote, title, item_id)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (digest_id, target, entry_key) do update set vote = excluded.vote,
                     item_id = coalesce(digest_feedback.item_id, excluded.item_id), created_at = now()""",
            (digest_id, target, key, vote, thing.get("title"), item_id),
        )
    return {"ok": True, "item_id": item_id}
