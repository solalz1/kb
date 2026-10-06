"""What the user cares about, learned from the KB itself.

Signals: tags and entities of saved items (recent and pinned ones count more), authors of saved tweets, Perso goals,
feedback on past digests, the people and feeds they follow, and a few sentences they can write themselves.
Claude turns that into a short profile used to pick and rank the news, and suggests engineers worth following.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from .. import db, llm
from ..config import get_settings

log = logging.getLogger(__name__)

PROFILE_KEY = "interest_profile"
MANUAL_KEY = "interests_manual"
MAX_AGE = timedelta(days=7)
AUTO_FOLLOW_MIN_SAVES = 2          # saved tweets from the same author before following them automatically

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "5 à 8 phrases : qui il est, ce qu'il veut suivre, à quel niveau."},
        "topics": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "weight": {"type": "integer", "enum": [1, 2, 3]}},
                "required": ["name", "weight"]},
        },
        "avoid": {"type": "array", "items": {"type": "string"}},
        "level": {"type": "string", "description": "Niveau technique attendu, en une phrase."},
        "people": {
            "type": "array",
            "description": "Ingénieurs, chercheurs ou auteurs qu'il gagnerait à suivre et qu'il ne suit pas encore.",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "x_handle": {"type": "string"}, "why": {"type": "string"}},
                "required": ["name", "x_handle", "why"]},
        },
    },
    "required": ["summary", "topics", "avoid", "level", "people"],
}


def _setting(key: str) -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (key,))
    return dict(row["value"]) if row else {}


def _save_setting(key: str, value: dict) -> None:
    db.execute(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (key, db.jsonb(value)),
    )


def manual_text() -> str:
    return _setting(MANUAL_KEY).get("text", "")


def set_manual_text(text: str) -> None:
    _save_setting(MANUAL_KEY, {"text": (text or "").strip()[:4000]})


def signals() -> dict:
    """Raw material for the profile, straight from the database."""
    tags = db.fetchall(
        """select tag, round(sum((case when pinned then 2 else 1 end)
                     * exp(-extract(epoch from now() - created_at) / 86400.0 / 90)))::int as w
           from items, unnest(tags) tag where status = 'ready' and not archived and space = 'main'
           group by tag order by w desc, tag limit 40""")
    entities = db.fetchall(
        """select e->>'name' as name, e->>'type' as type, count(*)::int as n
           from items, jsonb_array_elements(entities) e
           where status = 'ready' and not archived and space = 'main' and e->>'type' in ('person', 'organization', 'product', 'concept')
           group by 1, 2 order by n desc limit 40""")
    authors = db.fetchall(
        """select author, count(*)::int as n from items
           where kind = 'tweet' and status = 'ready' and author is not null
           group by author order by n desc limit 30""")
    recent = db.fetchall(
        """select title from items where status = 'ready' and space = 'main' and title is not null
           order by created_at desc limit 25""")
    goals = db.fetchall(
        """select title, left(coalesce(summary, input_text), 300) as summary from items
           where space = 'perso' and category = 'objectif' and not archived order by pinned desc, created_at desc limit 8""")
    feedback = db.fetchall(
        """select target, vote, title from digest_feedback
           where created_at > now() - interval '60 days' and title is not null order by created_at desc limit 60""")
    following = db.fetchall("select kind, name, x_handle, status from watch where status <> 'suggested' order by id")
    return {"tags": tags, "entities": entities, "authors": authors, "recent": recent, "goals": goals,
            "feedback": feedback, "following": following, "manual": manual_text()}


def _handle(author: str | None) -> str | None:
    m = re.search(r"@(\w{1,15})", author or "")
    return m.group(1) if m else None


def auto_follow(sig: dict) -> int:
    """Follow the people whose tweets the user keeps saving (they can be muted in the app)."""
    added = 0
    for a in sig["authors"]:
        handle = _handle(a["author"])
        if not handle or handle.lower() == "i" or a["n"] < AUTO_FOLLOW_MIN_SAVES:   # "@i": author not resolved
            continue
        name = re.sub(r"\s*\(@\w+\)\s*$", "", a["author"]).strip() or handle
        row = db.fetchone(
            """insert into watch (kind, name, x_handle, origin, status, note)
               values ('person', %s, %s, 'auto', 'active', %s)
               on conflict do nothing returning id""",
            (name, handle, f"{a['n']} tweets sauvegardés dans ta KB"),
        )
        added += bool(row)
    return added


def _prompt(sig: dict) -> str:
    def lines(rows, fmt):
        return "\n".join(fmt(r) for r in rows) or "(rien)"

    fb = sig["feedback"]
    return "\n\n".join([
        "Ce qu'il écrit lui-même sur ses centres d'intérêt :\n" + (sig["manual"] or "(rien)"),
        "Tags de sa veille (poids : récence, épinglés) :\n" + lines(sig["tags"], lambda r: f"- {r['tag']} ({r['w']})"),
        "Personnes, organisations, produits, concepts qui reviennent :\n"
        + lines(sig["entities"], lambda r: f"- {r['name']} [{r['type']}] ×{r['n']}"),
        "Auteurs des tweets qu'il sauvegarde :\n" + lines(sig["authors"], lambda r: f"- {r['author']} ×{r['n']}"),
        "Derniers éléments sauvegardés :\n" + lines(sig["recent"], lambda r: f"- {r['title']}"),
        "Ses objectifs (espace Perso) :\n" + lines(sig["goals"], lambda r: f"- {r['title']} : {r['summary']}"),
        "Ce qu'il a aimé dans les digests :\n" + lines([f for f in fb if f["vote"] > 0], lambda r: f"- [{r['target']}] {r['title']}"),
        "Ce qu'il n'a pas voulu :\n" + lines([f for f in fb if f["vote"] < 0], lambda r: f"- [{r['target']}] {r['title']}"),
        "Sources qu'il suit déjà :\n" + lines(sig["following"], lambda r: f"- {r['name']}"
                                               + (f" (@{r['x_handle']})" if r["x_handle"] else "") + f" [{r['status']}]"),
    ])


def compute(*, force: bool = False) -> dict:
    """Refresh the profile if it's missing, stale or forced. Returns it."""
    current = _setting(PROFILE_KEY)
    fresh = current.get("computed_at") and datetime.fromisoformat(current["computed_at"]) > datetime.now(timezone.utc) - MAX_AGE
    if fresh and not force:
        return current
    sig = signals()
    auto_follow(sig)
    sig["following"] = db.fetchall("select kind, name, x_handle, status from watch where status <> 'suggested' order by id")
    out = llm.call_tool(
        system=(
            f"Tu construis le profil d'intérêts d'un utilisateur pour son agent de veille tech quotidienne, à partir de "
            f"ce qu'il sauvegarde dans sa knowledge base. Écris en {llm.lang_name()}. Sois précis (« post-training des "
            f"LLM », « évaluation et benchmarks », pas « l'IA »). Déduis son niveau. Les retours négatifs vont dans avoid. "
            f"Propose 3 à 8 ingénieurs ou chercheurs actifs sur X qu'il ne suit pas encore, dont le travail recoupe ses "
            f"sujets, avec leur identifiant X exact (sans @) ; n'invente pas d'identifiant : si tu n'es pas sûr, ne propose "
            f"pas la personne. Si les signaux sont maigres, pars de ce qu'il écrit lui-même."
        ),
        content=_prompt(sig),
        tool_name="save_profile",
        tool_description="Enregistre le profil d'intérêts.",
        schema=PROFILE_SCHEMA,
        model=get_settings().enrich_model,
        max_tokens=2500,
    )
    for p in out.get("people") or []:
        handle = (p.get("x_handle") or "").strip().lstrip("@")
        if re.fullmatch(r"\w{1,15}", handle):
            db.execute(
                """insert into watch (kind, name, x_handle, origin, status, note)
                   values ('person', %s, %s, 'suggested', 'suggested', %s)
                   on conflict do nothing""",
                (p.get("name") or handle, handle, (p.get("why") or "")[:300]),
            )
    profile = {
        "summary": str(out.get("summary") or ""), "topics": _topics(out.get("topics")),
        "avoid": [str(a) for a in out.get("avoid") or [] if a], "level": str(out.get("level") or ""),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_setting(PROFILE_KEY, profile)
    return profile


def _weight(value) -> int:
    try:
        return min(3, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def _topics(raw) -> list[dict]:
    """The API doesn't enforce the schema: keep only well-formed topics, weights clamped to 1-3."""
    return [{"name": str(t["name"]), "weight": _weight(t.get("weight"))}
            for t in raw or [] if isinstance(t, dict) and t.get("name")]


def get() -> dict:
    return _setting(PROFILE_KEY)


def as_text(profile: dict) -> str:
    topics = ", ".join(t["name"] + (" " + "★" * (_weight(t.get("weight")) - 1) if _weight(t.get("weight")) > 1 else "")
                       for t in _topics(profile.get("topics")))
    return "\n".join(x for x in [
        profile.get("summary", ""),
        f"Sujets : {topics}" if topics else "",
        f"Niveau : {profile['level']}" if profile.get("level") else "",
        f"À éviter : {', '.join(profile['avoid'])}" if profile.get("avoid") else "",
        f"Il écrit : {manual_text()}" if manual_text() else "",
    ] if x)
