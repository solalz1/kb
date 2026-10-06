"""Serveur MCP : ta knowledge base comme connecteur dans Claude (web, desktop, mobile) et Claude Code."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import anyio
from mcp.server.mcpserver import MCPServer

from . import chat, db, pipeline, search
from .formatting import item_markdown
from .search import ITEM_FIELDS
from .taxonomy import normalize_category, normalize_space

INSTRUCTIONS = """Knowledge base personnelle de l'utilisateur, en deux espaces :
- Veille (space="main") : tweets, articles, vidéos, PDF, images, notes qu'il a sauvegardés, chacun résumé, taggé, indexé.
- Perso (space="perso") : son développement personnel. Ses principes, valeurs, leçons, objectifs, habitudes,
  réflexions, son journal, des citations et ressources qui l'inspirent.

Outils :
- kb_overview donne la carte de la KB (volume, types, tags, personnes et outils les plus présents) : utile pour
  savoir quoi chercher.
- search_kb : recherche sémantique + mots-clés, à relancer avec d'autres formulations si besoin. find_for_project quand
  l'utilisateur démarre un projet ou demande « qu'est-ce qui peut me servir pour… ».
- browse_kb : parcours exhaustif par espace, catégorie, type, tag, entité ou période.
- get_item pour lire un élément en entier avant d'en tirer une conclusion précise.
- get_principles quand il demande un conseil sur sa vie, une décision personnelle ou une situation difficile : fonde
  ton conseil sur SES principes et SES valeurs (cite-les), signale quand deux principes se contredisent, ne fais pas
  de morale, et propose de parler à un professionnel si la situation touche à sa santé ou à une crise.
- Cite toujours les éléments utilisés avec leur URL d'origine (« Source originale ») et, si utile, le lien « Fiche KB ».
- Distingue clairement ce qui vient de sa KB de tes connaissances générales.
- get_digest : son digest tech du jour (kind="daily") ou de la semaine (kind="weekly"), avec les projets proposés ;
  get_interests : ce que son agent de veille a compris de ses goûts et les ingénieurs qu'il suit.
- add_to_kb ajoute un lien ou une note (space="perso" et une catégorie pour une note de développement personnel) ;
  ne l'utilise que si l'utilisateur le demande."""

mcp = MCPServer("Knowledge base", instructions=INSTRUCTIONS)


def _run(fn, *args, **kwargs):
    return anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))


def _render(items: list[dict], empty: str) -> str:
    if not items:
        return empty
    return "\n\n".join(item_markdown(it) for it in items)


def _space(value: str | None) -> list[str] | None:
    key = normalize_space(value) if value and value.lower() not in ("all", "tout", "tous") else None
    return [key] if key else None


@mcp.tool()
async def search_kb(
    query: str,
    limit: int = 8,
    kinds: list[str] | None = None,
    tags: list[str] | None = None,
    space: str | None = None,
) -> str:
    """Recherche sémantique + mots-clés dans la knowledge base.

    Args:
        query: question ou sujet en langage naturel (français ou anglais).
        limit: nombre d'éléments (1-20).
        kinds: filtre facultatif parmi tweet, article, youtube, video, audio, pdf, image, document, note, repo, paper.
        tags: filtre facultatif sur des tags exacts.
        space: "main" (veille), "perso" (développement personnel) ; par défaut les deux.
    """
    try:
        spaces = _space(space)
    except ValueError as exc:
        return str(exc)
    items = await _run(search.search_items, query, limit=max(1, min(limit, 20)), kinds=kinds, tags=tags, spaces=spaces)
    return _render(items, "Aucun élément pertinent dans la knowledge base.")


@mcp.tool()
async def get_principles(situation: str | None = None) -> str:
    """Charte personnelle de l'utilisateur : tous ses principes et ses valeurs (texte intégral), et, si une situation
    est décrite, ses notes personnelles (leçons, réflexions, objectifs…) et éléments de veille qui s'en rapprochent.

    À appeler avant tout conseil de vie, de décision personnelle ou de conduite à tenir.

    Args:
        situation: la situation ou la décision, en quelques phrases (facultatif).
    """
    if situation and situation.strip():
        charter, related = await _run(chat.gather_for_advice, situation.strip())
    else:
        charter, related = await _run(chat.charter_items), []
    parts = []
    if charter:
        parts.append("## Charte : principes et valeurs\n\n" + "\n\n".join(
            item_markdown({**it, "content": (it.get("excerpts") or [""])[0], "excerpts": []}, with_content=True)
            for it in charter))
    else:
        parts.append("## Charte\n\nAucun principe ni aucune valeur notés pour l'instant (espace Perso, catégories "
                     "Principe et Valeur). Propose-lui d'en formuler.")
    if related:
        parts.append("## Notes et éléments liés à la situation\n\n" + _render(related, ""))
    return "\n\n".join(parts)


@mcp.tool()
async def get_item(item_id: str, include_full_content: bool = False) -> str:
    """Fiche complète d'un élément (résumé, points clés, source, éléments liés ; contenu intégral sur demande)."""
    def load():
        it = db.fetchone(f"select {ITEM_FIELDS}, content, status, error from items where id = %s", (item_id,))
        if not it:
            return None, []
        links = _links(item_id)
        return it, links

    it, links = await _run(load)
    if not it:
        return "Élément introuvable."
    if it["status"] != "ready":
        return f"Élément en cours de traitement (statut : {it['status']}). {it.get('error') or ''}"
    text = item_markdown(it, full=True, with_content=include_full_content)
    if links:
        text += "\n\nÉléments liés :\n" + "\n".join(f"- {l['title']} (id : {l['id']}) — {l['reason']}" for l in links)
    return text


def _links(item_id: str) -> list[dict]:
    return db.fetchall(
        """select i.id::text, i.title, l.reason, l.similarity from item_links l
           join items i on i.id = case when l.source_id = %(id)s then l.target_id else l.source_id end
           where (l.source_id = %(id)s or l.target_id = %(id)s) and i.status = 'ready'
           order by l.similarity desc limit 10""",
        {"id": item_id},
    )


@mcp.tool()
async def find_for_project(project_description: str, limit: int = 15) -> str:
    """Pour un nouveau projet : cherche sous plusieurs angles tout ce qui peut servir dans la KB.

    Renvoie les éléments classés avec résumé, cas d'usage et extraits ; à toi d'en faire la synthèse pour le projet.
    """
    plan, items = await _run(chat.gather_for_project, project_description, max(3, min(limit, 25)))
    header = (
        f"Projet compris : {plan.get('project_summary', '')}\n"
        f"Requêtes lancées : {' | '.join(plan.get('queries', []))}\n\n"
    )
    return header + _render(items, "Rien de pertinent dans la KB pour ce projet.")


@mcp.tool()
async def add_to_kb(
    url: str | None = None,
    text: str | None = None,
    note: str | None = None,
    space: str | None = None,
    category: str | None = None,
    title: str | None = None,
) -> str:
    """Ajoute un lien (tweet, article, vidéo, PDF…) ou une note texte à la knowledge base.

    Args:
        url: lien à capturer.
        text: texte d'une note (si pas d'URL), gardé tel quel.
        note: pourquoi l'utilisateur garde cet élément (facultatif).
        space: "main" (veille, par défaut) ou "perso" (développement personnel).
        category: pour space="perso" : principe, valeur, lecon, objectif, habitude, reflexion, journal, citation, ressource.
        title: titre de la note (facultatif ; sinon Claude en propose un).
    """
    try:
        res = await _run(pipeline.ingest, url=url, text=text, note=note, space=space, category=category, title=title)
    except ValueError as exc:
        return f"Impossible : {exc}"
    if res["duplicate"]:
        return f"Déjà dans la KB (id : {res['id']})."
    return f"Ajouté (id : {res['id']}). Traitement en cours : résumé et indexation dans une à deux minutes."


@mcp.tool()
async def list_recent(days: int = 7, limit: int = 20, kind: str | None = None) -> str:
    """Éléments sauvegardés récemment (par défaut : 7 derniers jours)."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    rows = await _run(
        db.fetchall,
        f"""select {ITEM_FIELDS} from items where status = 'ready' and not archived and created_at >= %s
            and (%s::text is null or kind = %s) order by created_at desc limit %s""",
        (since, kind, kind, max(1, min(limit, 50))),
    )
    return _render(rows, "Rien de sauvegardé sur cette période.")


@mcp.tool()
async def browse_kb(
    kind: str | None = None,
    tag: str | None = None,
    entity: str | None = None,
    since_days: int | None = None,
    space: str | None = None,
    category: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> str:
    """Liste exhaustive (paginée) des éléments filtrés par espace, catégorie, type, tag, entité (personne, outil,
    concept) ou période.

    À utiliser quand il faut couvrir tout un sujet plutôt que les meilleurs résultats d'une recherche
    (ex. space="perso", category="lecon" pour toutes ses leçons). Renvoie une ligne par élément ; get_item pour le détail.
    """
    try:
        spaces, cat = _space(space), normalize_category(category)
    except ValueError as exc:
        return str(exc)

    def load():
        where, params = ["status = 'ready'", "not archived"], []
        if spaces:
            where.append("space = %s")
            params.append(spaces[0])
        if cat:
            where.append("category = %s")
            params.append(cat)
        if kind:
            where.append("kind = %s")
            params.append(kind)
        if tag:
            where.append("%s = any(tags)")
            params.append(tag.lower().lstrip("#"))
        if entity:
            where.append("exists (select 1 from jsonb_array_elements(entities) e where lower(e->>'name') = lower(%s))")
            params.append(entity)
        if since_days:
            where.append("created_at >= now() - make_interval(days => %s)")
            params.append(since_days)
        clause = " and ".join(where)
        total = db.fetchone(f"select count(*)::int n from items where {clause}", params)["n"]
        rows = db.fetchall(
            f"""select id::text, kind, title, source_url, author, created_at, left(summary, 220) as summary,
                       space, category
                from items where {clause} order by created_at desc limit %s offset %s""",
            (*params, max(1, min(limit, 100)), max(0, offset)),
        )
        return total, rows

    total, rows = await _run(load)
    if not rows:
        return "Aucun élément ne correspond à ces filtres."
    lines = [f"{total} élément(s) au total ; affichés {offset + 1} à {offset + len(rows)}."]
    for r in rows:
        label = r["kind"] if r["space"] != "perso" else f"perso{'/' + r['category'] if r['category'] else ''}"
        lines.append(
            f"- [{label}] {r['title'] or '(sans titre)'} ({str(r['created_at'])[:10]}"
            f"{', ' + r['author'] if r['author'] else ''}) id={r['id']} {r['source_url'] or ''}\n  {r['summary'] or ''}"
        )
    if offset + len(rows) < total:
        lines.append(f"Suite : rappelle browse_kb avec offset={offset + len(rows)}.")
    return "\n".join(lines)


@mcp.tool()
async def kb_overview() -> str:
    """Vue d'ensemble de la KB : nombre d'éléments par type, tags et entités les plus fréquents, derniers ajouts."""
    def load():
        kinds = db.fetchall("select kind, count(*)::int n from items where status='ready' and not archived group by kind order by n desc")
        tags = db.fetchall(
            """select tag, count(*)::int n from items, unnest(tags) tag where status='ready' and not archived
               group by tag order by n desc limit 40""")
        ents = db.fetchall(
            """select e->>'name' as name, e->>'type' as type, count(*)::int n from items, jsonb_array_elements(entities) e
               where status='ready' and not archived group by 1, 2 order by n desc limit 30""")
        last = db.fetchone("select max(created_at) as d, count(*)::int n from items where status='ready' and not archived")
        perso = db.fetchall(
            """select coalesce(category, 'sans catégorie') as category, count(*)::int n from items
               where status='ready' and not archived and space = 'perso' group by 1 order by n desc""")
        return kinds, tags, ents, last, perso

    kinds, tags, ents, last, perso = await _run(load)
    if not last or not last["n"]:
        return "La KB est vide pour l'instant."
    n_perso = sum(p["n"] for p in perso)
    return "\n".join([
        f"{last['n']} éléments (veille {last['n'] - n_perso}, perso {n_perso}), dernier ajout le {str(last['d'])[:10]}.",
        "Perso par catégorie : " + (", ".join(f"{p['category']} {p['n']}" for p in perso) or "vide"),
        "Par type : " + ", ".join(f"{k['kind']} {k['n']}" for k in kinds),
        "Tags principaux : " + ", ".join(f"{t['tag']} ({t['n']})" for t in tags),
        "Personnes, outils, concepts : " + ", ".join(f"{e['name']} [{e['type']}] ({e['n']})" for e in ents),
    ])


@mcp.tool()
async def get_digest(kind: str = "daily", date: str | None = None) -> str:
    """Digest tech de l'utilisateur : celui du jour (kind="daily") ou de la semaine (kind="weekly", avec des projets à
    entreprendre), du plus général au plus technique, chaque élément avec son lien.

    Args:
        kind: "daily" ou "weekly".
        date: AAAA-MM-JJ pour un digest passé (jour du digest, ou premier jour de la semaine couverte) ; par défaut le dernier.
    """
    if kind not in ("daily", "weekly"):
        return "kind doit valoir daily ou weekly."

    def load():
        if date:
            return db.fetchone("select * from digests where kind = %s and period_start = %s::date", (kind, date))
        return db.fetchone(
            "select * from digests where kind = %s and status = 'ready' order by period_start desc limit 1", (kind,))

    try:
        row = await _run(load)
    except Exception:
        return "Date invalide (format AAAA-MM-JJ)."
    if not row:
        return "Aucun digest pour cette période. Il est généré chaque matin si DIGEST_ENABLED=true."
    if row["status"] != "ready":
        return f"Digest en cours de génération ou en erreur ({row['status']}). {row.get('error') or ''}"
    return row["content"] or ""


@mcp.tool()
async def get_interests() -> str:
    """Ce que l'agent de veille a compris des goûts de l'utilisateur, et les personnes et flux qu'il suit."""
    from .digest import profile

    def load():
        return profile.get(), db.fetchall(
            "select kind, name, x_handle, feed_url from watch where status = 'active' order by kind, name")

    prof, watch = await _run(load)
    lines = [profile.as_text(prof) if prof else "Profil pas encore calculé."]
    people = [w for w in watch if w["kind"] == "person"]
    feeds = [w for w in watch if w["kind"] == "feed"]
    if people:
        lines.append("Personnes suivies : " + ", ".join(
            f"{w['name']}" + (f" (@{w['x_handle']})" if w["x_handle"] else "") for w in people))
    if feeds:
        lines.append("Flux suivis : " + ", ".join(w["name"] for w in feeds))
    return "\n".join(lines)


@mcp.tool()
async def get_related(item_id: str) -> str:
    """Éléments reliés automatiquement à un élément donné, avec la raison du lien."""
    links = await _run(_links, item_id)
    if not links:
        return "Aucun lien pour cet élément."
    return "\n".join(f"- {l['title']} (id : {l['id']}, similarité {l['similarity']:.2f}) — {l['reason']}" for l in links)


@mcp.tool()
async def list_actions(include_done: bool = False, limit: int = 30) -> str:
    """Actions extraites de la KB : outils à tester, papiers à lire, comptes à suivre…"""
    rows = await _run(
        db.fetchall,
        """select a.id, a.text, a.kind, a.done, i.title, i.id::text item_id, i.source_url from actions a
           join items i on i.id = a.item_id where (%s or not a.done) order by a.created_at desc limit %s""",
        (include_done, max(1, min(limit, 100))),
    )
    if not rows:
        return "Aucune action en attente."
    return "\n".join(
        f"- [{'x' if r['done'] else ' '}] ({r['kind']}) {r['text']} — de « {r['title']} » {r['source_url'] or ''} (item {r['item_id']})"
        for r in rows
    )


@mcp.tool()
async def resurface(count: int = 5) -> str:
    """Fait remonter des éléments anciens, peu consultés et liés à ce que l'utilisateur sauvegarde en ce moment."""
    rows = await _run(resurface_items, max(1, min(count, 15)))
    return _render(rows, "Pas assez d'éléments pour une redécouverte.")


def resurface_items(count: int = 5, space: str | None = None) -> list[dict]:
    """Items de plus de 3 semaines proches des sauvegardes récentes, sinon pris au hasard."""
    rows = db.fetchall(
        f"""with recent as (
              select c.embedding from chunks c join items i on i.id = c.item_id
              where c.chunk_index = -1 and i.created_at > now() - interval '14 days' and i.status = 'ready'
              order by i.created_at desc limit 10
            ), candidates as (
              select c.item_id, max(1 - (c.embedding <=> r.embedding)) sim
              from chunks c cross join recent r join items i on i.id = c.item_id
              where c.chunk_index = -1 and i.status = 'ready' and not i.archived
                and i.created_at < now() - interval '21 days'
                and (i.last_viewed_at is null or i.last_viewed_at < now() - interval '30 days')
                and (%s::text is null or i.space = %s)
              group by c.item_id order by sim desc limit %s
            )
            select {ITEM_FIELDS} from items where id in (select item_id from candidates)""",
        (space, space, count),
    )
    if len(rows) < count:
        seen = [r["id"] for r in rows]
        rows += db.fetchall(
            f"""select {ITEM_FIELDS} from items where status = 'ready' and not archived
                and created_at < now() - interval '21 days' and not (id::text = any(%s))
                and (%s::text is null or space = %s)
                order by random() limit %s""",
            (seen, space, space, count - len(rows)),
        )
    return rows
