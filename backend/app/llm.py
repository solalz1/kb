"""Appels à Claude : enrichissement, vision, liens, reformulation, planification."""

from __future__ import annotations

import base64
import io
import logging
from datetime import date
from functools import lru_cache
from typing import Any, Iterator

import anthropic

from .config import get_settings
from .taxonomy import CATEGORIES

log = logging.getLogger(__name__)

LANG_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "it": "italien", "de": "allemand"}

ENTITY_TYPES = ["person", "organization", "product", "concept", "place", "work", "other"]
ACTION_KINDS = ["try", "read", "watch", "follow", "buy", "do"]
GENRES = [
    "thread", "opinion", "news", "tutorial", "paper", "tool", "announcement", "talk", "podcast",
    "course", "reference", "dataset", "inspiration", "personal-note", "other",
]


def lang_name() -> str:
    code = get_settings().kb_language
    return LANG_NAMES.get(code, code)


@lru_cache
def client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY manquant")
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=180)


def call_tool(
    *,
    system: str,
    content: Any,
    tool_name: str,
    tool_description: str,
    schema: dict,
    model: str | None = None,
    max_tokens: int = 2000,
) -> dict:
    """Appel avec un outil imposé : renvoie directement le JSON structuré."""
    kwargs = dict(
        model=model or get_settings().enrich_model,
        max_tokens=max_tokens,
        system=system,
        tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": content}],
    )
    if max_tokens > 4000:
        # long outputs (digests) are streamed so they never hit the HTTP timeout
        with client().messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
    else:
        resp = client().messages.create(**kwargs)
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"Réponse de Claude tronquée ({tool_name}, max_tokens={max_tokens})")
    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise RuntimeError("Claude n'a pas renvoyé de résultat structuré")


def complete(*, system: str, prompt: str, model: str | None = None, max_tokens: int = 400) -> str:
    resp = client().messages.create(
        model=model or get_settings().enrich_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def stream_text(*, system: str, messages: list[dict], model: str | None = None, max_tokens: int = 4000) -> Iterator[str]:
    with client().messages.stream(
        model=model or get_settings().chat_model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    ) as stream:
        yield from stream.text_stream


# ---------------------------------------------------------------------------
# Enrichissement d'un item
# ---------------------------------------------------------------------------

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Titre informatif, 90 caractères max."},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ENTITY_TYPES}},
                "required": ["name", "type"],
            },
        },
        "use_cases": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": ACTION_KINDS}},
                "required": ["text", "kind"],
            },
        },
        "genre": {"type": "string", "enum": GENRES},
        "language": {"type": "string", "description": "Code ISO 639-1 de la langue du contenu original."},
    },
    "required": ["title", "summary", "key_points", "tags", "entities", "use_cases", "action_items", "genre", "language"],
}


def _enrich_system() -> str:
    lang = lang_name()
    return f"""Tu es l'archiviste de la knowledge base personnelle de l'utilisateur.
Pour chaque élément capturé, tu produis une fiche qui servira plus tard à : (1) le retrouver par la recherche,
(2) répondre à des questions, (3) le suggérer quand l'utilisateur démarre un nouveau projet.

Règles :
- Écris en {lang}. Exception : si l'élément a déjà un bon titre dans sa langue d'origine, garde-le tel quel.
- Factuel et dense. Pas de remplissage ni de formules du type « Cet article explique… » : va droit au contenu.
- summary : 2 à 5 phrases sur ce que l'élément affirme, montre ou propose, avec les chiffres, noms et conclusions qui comptent.
- key_points : 3 à 7 points autonomes et précis, compréhensibles sans le reste.
- tags : 3 à 8 tags en minuscules, au singulier, mots simples ou kebab-case. Réutilise d'abord les tags existants
  quand ils conviennent ; n'en crée un nouveau que si aucun ne convient.
- entities : personnes, organisations, produits/outils, concepts, œuvres importants et explicitement mentionnés
  (12 max), avec leur nom canonique (« Andrej Karpathy », pas « Karpathy »).
- use_cases : 2 à 4 situations concrètes où l'élément serait utile, formulées « Utile pour… » ou « Utile si… ».
- action_items : uniquement des actions concrètes suggérées par le contenu (outil à tester, papier à lire,
  compte à suivre…). Liste vide sinon.
- Si l'utilisateur a dit pourquoi il garde l'élément, oriente le résumé et les use_cases vers cette intention.
- Si le contenu est vide, tronqué ou inaccessible, dis-le dans le summary au lieu d'inventer."""


def _perso_rules() -> str:
    cats = "\n".join(f"  - {key} : {desc}" for key, (_, _, desc) in CATEGORIES.items())
    return f"""

Cet élément est dans l'espace PERSO de l'utilisateur (développement personnel). Il servira surtout à lui donner des
conseils fidèles à SES principes et SES valeurs. Donc :
- Si c'est une note qu'il a écrite, reste fidèle à sa pensée et à ses mots : reformule sans interpréter, sans juger,
  sans ajouter de conseils. Écris à la deuxième personne (« tu »). Le titre résume l'idée en quelques mots.
- key_points : les idées, règles ou engagements tels qu'il les formule.
- use_cases : les situations concrètes où ce principe, cette leçon ou cette ressource devrait guider une décision
  (« Utile quand… »).
- action_items : uniquement ses engagements explicites, sinon liste vide.
- category : la catégorie qui convient le mieux :
{cats}"""


def _truncate_middle(text: str, limit: int = 60_000) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.75), int(limit * 0.2)
    return text[:head] + "\n\n[… contenu tronqué …]\n\n" + text[-tail:]


def enrich(
    *,
    kind: str,
    title: str | None,
    author: str | None,
    source_url: str | None,
    published_at: str | None,
    content: str,
    user_note: str | None,
    existing_tags: list[str],
    space: str = "main",
    category: str | None = None,
) -> dict:
    perso = space == "perso"
    header = [
        f"Type : {kind}" + (" (note écrite par l'utilisateur)" if kind == "note" else ""),
        f"Espace : {'Perso (développement personnel)' if perso else 'Veille'}"
        + (f" ; catégorie choisie : {CATEGORIES[category][0]}" if category in CATEGORIES else ""),
        f"Titre actuel : {title or '(aucun)'}",
        f"Auteur : {author or '(inconnu)'}",
        f"Source : {source_url or '(fichier ou note)'}",
        f"Date de publication : {published_at or '(inconnue)'}",
        f"Pourquoi l'utilisateur le garde : {user_note or '(non précisé)'}",
        "Tags existants (à réutiliser si pertinents) : " + (", ".join(existing_tags) if existing_tags else "(aucun)"),
    ]
    prompt = "\n".join(header) + "\n\n<contenu>\n" + _truncate_middle(content or "(vide)") + "\n</contenu>"
    schema = ENRICH_SCHEMA
    if perso:
        schema = {
            **ENRICH_SCHEMA,
            "properties": {**ENRICH_SCHEMA["properties"],
                           "category": {"type": "string", "enum": list(CATEGORIES)}},
            "required": ENRICH_SCHEMA["required"] + ["category"],
        }
    out = call_tool(
        system=_enrich_system() + (_perso_rules() if perso else ""),
        content=prompt,
        tool_name="save_card",
        tool_description="Enregistre la fiche de l'élément dans la knowledge base.",
        schema=schema,
        max_tokens=2500,
    )
    out["tags"] = _normalize_tags(out.get("tags", []))
    return out


def _normalize_tags(tags: list[str]) -> list[str]:
    seen, result = set(), []
    for t in tags:
        t = "-".join(str(t).strip().lower().lstrip("#").split())
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result[:10]


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

def prepare_image(data: bytes, media_type: str | None = None, max_side: int = 1568) -> tuple[bytes, str]:
    """Convertit (HEIC compris) et redimensionne une image pour l'API."""
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # pragma: no cover
        pass
    img = Image.open(io.BytesIO(data))
    img.load()
    if getattr(img, "is_animated", False):
        img.seek(0)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Titre court décrivant l'image."},
        "description": {"type": "string", "description": "Ce que montre l'image et ce qu'il faut en retenir."},
        "text_in_image": {"type": "string", "description": "Transcription fidèle de tout le texte lisible (vide si aucun)."},
        "source_author": {"type": "string", "description": "Auteur visible (ex. @handle d'un tweet capturé), sinon vide."},
        "source_platform": {"type": "string", "description": "Plateforme reconnue (X, LinkedIn, article…), sinon vide."},
        "source_url": {"type": "string", "description": "URL visible dans l'image, sinon vide."},
    },
    "required": ["title", "description", "text_in_image", "source_author", "source_platform", "source_url"],
}


def describe_image(data: bytes, media_type: str | None = None, context: str = "") -> dict:
    img, mt = prepare_image(data, media_type)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(img).decode()}},
        {
            "type": "text",
            "text": (
                f"Analyse cette image pour la knowledge base. Réponds en {lang_name()} (sauf la transcription, "
                f"qui reste dans la langue d'origine).\n"
                "Si c'est une capture d'écran (tweet, post, article, slide, code, graphique), transcris le texte "
                "intégralement et identifie la source si elle est visible."
                + (f"\nContexte : {context}" if context else "")
            ),
        },
    ]
    return call_tool(
        system="Tu décris des images avec précision pour une knowledge base personnelle.",
        content=content,
        tool_name="save_image_analysis",
        tool_description="Enregistre l'analyse de l'image.",
        schema=IMAGE_SCHEMA,
        max_tokens=3000,
    )


def describe_frames(frames: list[bytes], context: str = "") -> str:
    """Décrit une vidéo à partir de quelques images clés (utile quand il y a peu de paroles)."""
    content: list[dict] = []
    for f in frames[:6]:
        img, mt = prepare_image(f, "image/jpeg", max_side=1024)
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                    "data": base64.b64encode(img).decode()}})
    content.append({"type": "text", "text": (
        f"Ces images sont extraites dans l'ordre d'une vidéo. Décris en {lang_name()} ce que montre la vidéo et "
        f"transcris tout texte affiché à l'écran. Sois factuel." + (f"\nContexte : {context}" if context else "")
    )})
    resp = client().messages.create(model=get_settings().enrich_model, max_tokens=1500,
                                    messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def transcribe_pdf(data: bytes, first_page: int = 1) -> str:
    """Pour les PDF scannés : Claude lit un lot de pages (≈10) et transcrit le texte (en streaming : pas de timeout)."""
    with client().messages.stream(
        model=get_settings().enrich_model,
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                 "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": f"Transcris fidèlement le texte de ce document en Markdown, page par page. "
                                          f"La première page est la page {first_page} : marque chaque page par « [p. N] ». "
                                          f"Décris brièvement les figures importantes."},
            ],
        }],
    ) as stream:
        return stream.get_final_text()


# ---------------------------------------------------------------------------
# Liens entre items
# ---------------------------------------------------------------------------

LINKS_SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "related": {"type": "boolean"},
                    "reason": {"type": "string", "description": "Une phrase courte : en quoi les deux éléments sont liés."},
                },
                "required": ["id", "related", "reason"],
            },
        }
    },
    "required": ["links"],
}


def explain_links(item: dict, candidates: list[dict]) -> list[dict]:
    lines = [f"NOUVEL ÉLÉMENT\nTitre : {item.get('title')}\nRésumé : {item.get('summary')}\n", "CANDIDATS"]
    for c in candidates:
        lines.append(f"- id={c['id']} | {c.get('title')} | {(c.get('summary') or '')[:500]}")
    out = call_tool(
        system=(
            f"Tu relies les éléments d'une knowledge base. Pour chaque candidat, dis s'il a un lien réellement utile "
            f"avec le nouvel élément (même sujet précis, complément, contradiction, application, même auteur sur le même thème). "
            f"Un vague thème commun ne suffit pas. Explique le lien en une phrase courte en {lang_name()}."
        ),
        content="\n".join(lines),
        tool_name="save_links",
        tool_description="Enregistre les liens entre éléments.",
        schema=LINKS_SCHEMA,
        max_tokens=1500,
    )
    return [l for l in out.get("links", []) if l.get("related")]


# ---------------------------------------------------------------------------
# Chat : reformulation et planification de recherche
# ---------------------------------------------------------------------------

def rewrite_query(history: list[dict], question: str) -> str:
    convo = "\n".join(f"{m['role']}: {str(m['content'])[:800]}" for m in history[-6:])
    try:
        out = complete(
            system="Tu reformules la dernière question de l'utilisateur en une requête de recherche autonome "
                   "(mots-clés et entités explicites). Réponds uniquement par la requête.",
            prompt=f"Conversation :\n{convo}\n\nDernière question : {question}",
            max_tokens=150,
        )
        return out.strip().strip('"') or question
    except Exception:
        log.exception("Reformulation impossible")
        return question


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "project_summary": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}, "description": "4 à 8 requêtes variées."},
    },
    "required": ["project_summary", "queries"],
}


def plan_project_queries(description: str) -> dict:
    return call_tool(
        system=(
            f"L'utilisateur démarre un projet et veut savoir ce que sa knowledge base contient d'utile. "
            f"Résume le projet en une phrase ({lang_name()}), puis propose 4 à 8 requêtes de recherche variées qui couvrent : "
            f"le cœur du sujet, les techniques et outils probables, les risques et erreurs classiques, des exemples ou "
            f"concurrents, les personnes ou sources de référence, et des angles inattendus mais pertinents. "
            f"Mélange français et anglais si le sujet est technique. Date du jour : {date.today().isoformat()}."
        ),
        content=description,
        tool_name="plan_search",
        tool_description="Enregistre le plan de recherche.",
        schema=PLAN_SCHEMA,
        max_tokens=800,
    )
