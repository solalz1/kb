"""Chat avec la knowledge base (questions) et mode projet (dossier sourcé)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Iterator

from . import db, llm, search
from .formatting import source_block, source_card
from .search import ITEM_FIELDS
from .taxonomy import CHARTER_CATEGORIES

CONTEXT_BUDGET = 70_000  # caractères de sources envoyés au modèle
CHARTER_BUDGET = 30_000  # caractères de principes et valeurs (texte intégral) en mode Conseil


def _lang(lang: str | None) -> str:
    """The language Claude answers in: the app's (sent with each question), else the KB's."""
    return llm.LANG_NAMES.get(lang, llm.lang_name()) if lang else llm.lang_name()


def _system_common(lang: str | None = None) -> str:
    return f"""Tu es l'assistant de la knowledge base personnelle de l'utilisateur. Date du jour : {date.today().isoformat()}.
Les sources fournies sont des éléments que l'utilisateur a lui-même sauvegardés (tweets, articles, vidéos, PDF, notes…).

Règles :
- Réponds en {_lang(lang)}, de façon directe et structurée (Markdown léger, pas de titre inutile). Les titres de
  section imposés plus bas s'écrivent aussi dans cette langue.
- Après chaque affirmation tirée d'une source, mets sa référence entre crochets : [1] ou [2][5]. N'utilise que les numéros fournis.
- N'invente jamais de source, d'URL, de chiffre ou de citation.
- Si les sources ne suffisent pas, dis-le. Tu peux compléter avec tes connaissances générales, mais signale-le
  explicitement (« Hors KB : … ») et sans référence.
- Quand c'est utile, précise d'où vient l'info (« dans un thread de @x », « dans la vidéo de y »)."""


SYSTEM_PROJECT_EXTRA = """
L'utilisateur démarre un projet. Rédige un dossier à partir de SA knowledge base :

## En bref
2 à 3 phrases : ce que la KB apporte concrètement à ce projet (ou pas grand-chose, si c'est le cas).

## Ce qui peut te servir
Regroupe par thème (sous-titres ###). Pour chaque élément utile : ce qu'il apporte à CE projet (idée, méthode, outil,
chiffre, mise en garde, exemple) et sa référence [n]. Ignore les sources hors sujet.

## Personnes, outils et ressources à creuser
Liste courte, avec références.

## Angles morts
Ce que la KB ne couvre pas et qu'il faudrait chercher ailleurs (sans référence).

Sois concret et orienté action."""


SYSTEM_ADVICE_EXTRA = """
Mode Conseil. L'utilisateur te demande un conseil sur sa vie : une décision, une situation, une difficulté, un objectif.
Tu l'aides à décider selon SES principes et SES valeurs, tels qu'il les a notés dans l'espace Perso de sa KB,
et non selon des conseils génériques.

Les sources marquées « (charte) » sont ses principes et ses valeurs, en texte intégral. Les autres sont ses notes
personnelles (leçons, réflexions, objectifs, journal…) ou des éléments de sa veille qui peuvent éclairer la situation.
Ignore celles qui ne concernent pas la situation.

Structure ta réponse ainsi (en sautant une section vide) :
- Une phrase qui reformule l'enjeu tel que tu le comprends.
## Ce que disent tes principes
Les principes, valeurs et leçons qui s'appliquent vraiment ici, avec leur référence [n] et en quoi ils s'appliquent.
Reprends ses propres mots quand c'est parlant.
## Les tensions
Si deux de ses principes tirent dans des sens opposés : nomme la tension et propose une façon de trancher qui lui
ressemble (en t'appuyant sur ses priorités s'il en a noté).
## Mon conseil
Une recommandation claire et concrète, ou les deux ou trois options sérieuses et ce que chacune implique au regard de
ses valeurs. Si ses propres principes contredisent ce qu'il semble vouloir faire, dis-le franchement et avec tact.
## Prochain pas
Une ou deux actions concrètes, ou les questions à se poser.

Règles propres à ce mode :
- Tutoie-le. Ton direct et chaleureux. Pas de morale, pas de jugement, pas de platitudes de développement personnel.
- S'il n'a noté aucun principe pertinent, dis-le, réponds quand même en signalant « Hors KB », et suggère le principe
  qu'il pourrait formuler et noter.
- Si la situation est floue, pose au plus deux questions à la fin.
- Si la situation touche à sa santé, à une crise ou à un danger pour lui ou pour d'autres, dis-lui aussi d'en parler
  à un professionnel ou à une personne de confiance."""


def build_sources(items: list[dict], max_excerpt: int = 1800) -> tuple[list[dict], str]:
    cards, blocks, used = [], [], 0
    for n, it in enumerate(items, 1):
        block = source_block(n, it, max_excerpt=max_excerpt)
        if used + len(block) > CONTEXT_BUDGET and blocks:
            break
        used += len(block)
        blocks.append(block)
        cards.append(source_card(n, it))
    return cards, "\n\n".join(blocks)


def _clean_history(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages[-10:]:
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            content = re.sub(r"\s?\[\d+\]", "", content)
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def ask(messages: list[dict], kinds: list[str] | None = None, tags: list[str] | None = None,
        model: str | None = None, lang: str | None = None) -> Iterator[dict]:
    history = _clean_history(messages[:-1])
    question = str(messages[-1]["content"]).strip()
    query = llm.rewrite_query(history, question) if history else question
    yield {"type": "status", "text": "Recherche dans ta KB…"}
    items = search.search_items(query, limit=8, kinds=kinds, tags=tags)
    sources, context = build_sources(items)
    yield {"type": "sources", "query": query, "sources": sources}

    user_turn = (
        f"<sources>\n{context or '(aucune source trouvée dans la KB)'}\n</sources>\n\nQuestion : {question}"
    )
    msgs = history + [{"role": "user", "content": user_turn}]
    if len(msgs) > 1 and msgs[-2]["role"] == "user":
        msgs[-2:] = [{"role": "user", "content": msgs[-2]["content"] + "\n\n" + user_turn}]
    for text in llm.stream_text(system=_system_common(lang), messages=msgs, model=model):
        yield {"type": "delta", "text": text}
    yield {"type": "done"}


def charter_items(budget: int = CHARTER_BUDGET) -> list[dict]:
    """Principes et valeurs (espace Perso), en texte intégral, les épinglés d'abord."""
    rows = db.fetchall(
        f"""select {ITEM_FIELDS}, coalesce(content, input_text) as content from items
            where space = 'perso' and category = any(%s) and not archived
              and (status = 'ready' or (kind = 'note' and input_text is not null))  -- même en cours de retraitement
            order by pinned desc, array_position(%s, category), created_at""",
        (list(CHARTER_CATEGORIES), list(CHARTER_CATEGORIES)),
    )
    out, used = [], 0
    for r in rows:
        text = (r.pop("content") or "").strip()
        if used + len(text) > budget and out:
            text = ""          # au-delà du budget : le résumé seul
        used += len(text)
        r["excerpts"] = [text[:6000]] if text else []
        out.append(r)
    return out


def gather_for_advice(situation: str, query: str | None = None) -> tuple[list[dict], list[dict]]:
    """Charte complète + notes perso et éléments de veille proches de la situation."""
    query = query or situation
    charter = charter_items()
    seen = {c["id"] for c in charter}
    with ThreadPoolExecutor(max_workers=2) as pool:
        perso = pool.submit(search.search_items, query, limit=12, spaces=["perso"], chunks_per_item=2)
        main = pool.submit(search.search_items, query, limit=4, spaces=["main"], chunks_per_item=2)
        related = [it for it in perso.result() if it["id"] not in seen][:8] + main.result()
    return charter, related


def advise(messages: list[dict], model: str | None = None, lang: str | None = None) -> Iterator[dict]:
    history = _clean_history(messages[:-1])
    question = str(messages[-1]["content"]).strip()
    query = llm.rewrite_query(history, question) if history else question
    yield {"type": "status", "text": "Je relis tes principes et tes notes…"}
    charter, related = gather_for_advice(question, query)
    sources, context = build_sources(charter + related, max_excerpt=6000)
    yield {"type": "sources", "query": query, "sources": sources}

    intro = (f"Les sources 1 à {len(charter)} sont sa charte (principes et valeurs)." if charter
             else "Il n'a encore noté aucun principe ni aucune valeur (catégories Principe et Valeur de l'espace Perso).")
    user_turn = f"{intro}\n\n<sources>\n{context or '(aucune note pertinente)'}\n</sources>\n\nSituation : {question}"
    msgs = history + [{"role": "user", "content": user_turn}]
    if len(msgs) > 1 and msgs[-2]["role"] == "user":
        msgs[-2:] = [{"role": "user", "content": msgs[-2]["content"] + "\n\n" + user_turn}]
    for text in llm.stream_text(system=_system_common(lang) + SYSTEM_ADVICE_EXTRA, messages=msgs, model=model,
                                max_tokens=5000):
        yield {"type": "delta", "text": text}
    yield {"type": "done"}


def gather_for_project(description: str, limit: int = 14, kinds: list[str] | None = None) -> tuple[dict, list[dict]]:
    plan = llm.plan_project_queries(description)
    queries = [q for q in plan.get("queries", []) if q.strip()][:8] or [description]
    queries.append(description)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rankings = list(pool.map(lambda q: search.search_items(q, limit=8, kinds=kinds, chunks_per_item=2), queries))
    return plan, search.merge_rankings(rankings, limit=limit)


def project(description: str, kinds: list[str] | None = None, model: str | None = None,
            lang: str | None = None) -> Iterator[dict]:
    yield {"type": "status", "text": "Je prépare les recherches…"}
    plan, items = gather_for_project(description, kinds=kinds)
    yield {"type": "plan", "summary": plan.get("project_summary"), "queries": plan.get("queries", [])}
    sources, context = build_sources(items)
    yield {"type": "sources", "sources": sources}
    prompt = (
        f"<sources>\n{context or '(aucune source pertinente trouvée)'}\n</sources>\n\n"
        f"Projet : {description}\n\nRésumé du projet : {plan.get('project_summary', '')}"
    )
    for text in llm.stream_text(system=_system_common(lang) + SYSTEM_PROJECT_EXTRA,
                                messages=[{"role": "user", "content": prompt}], model=model, max_tokens=6000):
        yield {"type": "delta", "text": text}
    yield {"type": "done"}
