"""Appels à Claude : enrichissement, vision, liens, reformulation, planification."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import date
from functools import lru_cache
from typing import Any, Iterator

import anthropic

from . import costs, db
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


# ---------------------------------------------------------------------------
# Thinking
# ---------------------------------------------------------------------------

# Models that think by default (adaptive thinking); max_tokens counts the thinking, so they get room for it.
THINKS_BY_DEFAULT = ("claude-haiku-5", "claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-mythos-5")
THINKING_ROOM = 8000
THINKING_SETTING = "thinking"


def thinking_enabled() -> bool:
    """On by default; the user can turn it off in Settings."""
    try:
        row = db.fetchone("select value from kb_settings where key = %s", (THINKING_SETTING,))
    except Exception:  # noqa: BLE001 — a setting that can't be read keeps the default
        log.warning("Réglage de réflexion illisible", exc_info=True)
        return True
    return bool((row["value"] or {}).get("enabled", True)) if row else True


def set_thinking(enabled: bool) -> dict:
    db.execute(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (THINKING_SETTING, db.jsonb({"enabled": bool(enabled)})))
    return {"enabled": thinking_enabled()}


def _thinking(model: str) -> dict:
    """The `thinking` field of a request. With thinking on (the default), nothing: the models that think by default
    keep doing so. Off, it's turned off where the API allows it: Haiku 5.5 ("disabled", accepted at its default effort)
    and Sonnet 5.5 ("between_tools": no thinking before the answer). Opus 5.5 and Fable 5.1 always think."""
    if thinking_enabled():
        return {}
    if model.startswith("claude-haiku-5"):
        return {"thinking": {"type": "disabled"}}
    if model.startswith("claude-sonnet-5-5"):
        return {"thinking": {"type": "between_tools"}}
    return {}


def _room(model: str, max_tokens: int, thinking: dict) -> int:
    """max_tokens for the answer, plus room for the thinking when the model will think."""
    return max_tokens + THINKING_ROOM if model.startswith(THINKS_BY_DEFAULT) and not thinking else max_tokens


# Models that refuse a forced tool call (Sonnet 5.5, Opus 5.5 and later): they get structured outputs instead.
# Filled the first time a model answers « tool_choice … not supported », so the refused request happens once per run.
_NO_FORCED_TOOL: set[str] = set()

# JSON Schema keywords structured outputs don't accept (they only guide the model, so dropping them is harmless)
_UNSUPPORTED_KEYWORDS = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                         "multipleOf", "maxItems", "minProperties", "maxProperties", "default"}


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
    model = model or get_settings().enrich_model
    if model not in _NO_FORCED_TOOL:
        try:
            return _forced_tool(system=system, content=content, tool_name=tool_name,
                                tool_description=tool_description, schema=schema, model=model, max_tokens=max_tokens)
        except anthropic.BadRequestError as e:
            if not _refuses_forced_tool(e):
                raise
            _NO_FORCED_TOOL.add(model)
            log.info("%s refuses forced tool use: structured outputs from now on", model)
    return _structured_output(system=system, content=content, tool_name=tool_name,
                              tool_description=tool_description, schema=schema, model=model, max_tokens=max_tokens)


def _refuses_forced_tool(e: anthropic.BadRequestError) -> bool:
    text = str(e)
    return "tool_choice" in text and "not supported" in text


def _forced_tool(*, system, content, tool_name, tool_description, schema, model, max_tokens) -> dict:
    kwargs = dict(
        model=model,
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
    costs.record_claude(model, getattr(resp, "usage", None), tool_name)
    _check_stop(resp, tool_name, max_tokens)
    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise RuntimeError("Claude n'a pas renvoyé de résultat structuré")


def _structured_output(*, system, content, tool_name, tool_description, schema, model, max_tokens) -> dict:
    """Same result through structured outputs: the answer is JSON that follows the schema. These models always think
    first, and max_tokens counts the thinking too, so they get more room. Always streamed: thinking takes time."""
    budget = max(2 * max_tokens, max_tokens + THINKING_ROOM)
    with client().messages.stream(
        model=model,
        max_tokens=budget,
        system=f"{system}\n\nRéponds par un objet JSON conforme au schéma demandé. {tool_description}",
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": strict_schema(schema)}},
        **_thinking(model),
    ) as stream:
        resp = stream.get_final_message()
    costs.record_claude(model, getattr(resp, "usage", None), tool_name)
    _check_stop(resp, tool_name, budget)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        out = json.loads(text)
    except ValueError as e:
        raise RuntimeError(f"Claude n'a pas renvoyé de JSON valide ({tool_name})") from e
    if not isinstance(out, dict):
        raise RuntimeError(f"Claude n'a pas renvoyé d'objet JSON ({tool_name})")
    return out


def _check_stop(resp, tool_name: str, max_tokens: int) -> None:
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"Réponse de Claude tronquée ({tool_name}, max_tokens={max_tokens})")
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude a refusé de répondre ({tool_name})")


def strict_schema(schema: Any) -> Any:
    """A copy of a tool schema that structured outputs accept: every object closed with additionalProperties: false,
    unsupported keywords removed, minItems kept only at 0 or 1. Required fields are unchanged."""
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_KEYWORDS or (key == "minItems" and value not in (0, 1)):
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            out[key] = {name: strict_schema(sub) for name, sub in value.items()}
        elif key == "enum":
            out[key] = value
        else:
            out[key] = strict_schema(value)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out


def complete(*, system: str, prompt: str, model: str | None = None, max_tokens: int = 400) -> str:
    model = model or get_settings().enrich_model
    thinking = _thinking(model)
    resp = client().messages.create(
        model=model,
        max_tokens=_room(model, max_tokens, thinking),
        system=system,
        messages=[{"role": "user", "content": prompt}],
        **thinking,
    )
    costs.record_claude(model, getattr(resp, "usage", None), "complete")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def stream_text(*, system: str, messages: list[dict], model: str | None = None, max_tokens: int = 4000) -> Iterator[str]:
    model = model or get_settings().chat_model
    thinking = _thinking(model)
    with client().messages.stream(
        model=model,
        max_tokens=_room(model, max_tokens, thinking),   # the answer keeps max_tokens, thinking comes on top
        system=system,
        messages=messages,
        **thinking,
    ) as stream:
        yield from stream.text_stream
        costs.record_claude(model, getattr(stream.get_final_message(), "usage", None), "chat")


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


TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "use_cases": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "key_points", "use_cases"],
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
- tags : 3 à 8 tags EN ANGLAIS, quelle que soit la langue du contenu (ils servent à la recherche) : minuscules,
  singulier, mots simples ou kebab-case (« llm-evaluation », « benchmark », « fine-tuning »). Réutilise d'abord les
  tags existants qui sont en anglais quand ils conviennent ; n'en crée un nouveau que si aucun ne convient.
- entities : personnes, organisations, produits/outils, concepts, œuvres importants et explicitement mentionnés
  (12 max), avec leur nom canonique (« Andrej Karpathy », pas « Karpathy »).
- use_cases : 2 à 4 situations concrètes où l'élément serait utile, formulées « Utile pour… » ou « Utile si… ».
- action_items : uniquement des actions concrètes suggérées par le contenu (outil à tester, papier à lire,
  compte à suivre…). Liste vide sinon.
- Si l'utilisateur a dit pourquoi il garde l'élément, oriente le résumé et les use_cases vers cette intention.
- Si le contenu est vide, tronqué ou inaccessible, dis-le dans le summary au lieu d'inventer."""


def _translation_rule(second: str) -> str:
    return f"""
- translation : la même fiche en {LANG_NAMES.get(second, second)} pour les lecteurs de cette langue : title, summary,
  key_points et use_cases fidèles à la version principale (mêmes faits, mêmes chiffres, même longueur), écrits
  naturellement. Un titre propre à la source (titre d'article, de vidéo, de papier) se garde tel quel."""


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


NO_FOLDER = "aucun"


def _folder_property(folders: list[dict]) -> dict:
    return {"type": "string", "enum": [f["name"] for f in folders] + [NO_FOLDER]}


def _folder_list(folders: list[dict]) -> str:
    return "\n".join(f"  - {f['name']}" + (f" : {f['description']}" if f.get("description") else "") for f in folders)


def _folder_rules(folders: list[dict]) -> str:
    return f"""

- folder : le dossier de l'utilisateur où ranger l'élément, d'après son sujet principal (nom exact), ou « {NO_FOLDER} »
  si aucun ne convient vraiment. Ses dossiers :
{_folder_list(folders)}"""


FOLDERS_SCHEMA_ITEM = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id", "folder"]}


def classify_folders(items: list[dict], folders: list[dict]) -> dict[str, str]:
    """Files existing items in the user's folders: {item id: folder name}, items that fit none left out. One call for
    a batch of items (id, kind, title, summary, tags)."""
    lines = []
    for it in items:
        tags = ", ".join(it.get("tags") or [])
        lines.append(f"- id={it['id']} | {it.get('kind') or '?'} | {it.get('title') or '(sans titre)'}"
                     f" | {(it.get('summary') or '')[:400]}" + (f" | tags : {tags}" if tags else ""))
    item_schema = {**FOLDERS_SCHEMA_ITEM,
                   "properties": {**FOLDERS_SCHEMA_ITEM["properties"], "folder": _folder_property(folders)}}
    out = call_tool(
        system=(
            "Tu ranges les éléments de la knowledge base personnelle de l'utilisateur dans SES dossiers. Pour chaque "
            "élément, choisis le dossier qui correspond à son sujet principal (nom exact), ou "
            f"« {NO_FOLDER} » si aucun ne convient vraiment. Ses dossiers :\n{_folder_list(folders)}"
        ),
        content="ÉLÉMENTS\n" + "\n".join(lines),
        tool_name="file_items",
        tool_description="Enregistre le dossier de chaque élément.",
        schema={"type": "object", "properties": {"items": {"type": "array", "items": item_schema}},
                "required": ["items"]},
        max_tokens=200 + 60 * len(items),
    )
    names = {f["name"] for f in folders}
    ids = {it["id"] for it in items}
    rows = out.get("items") if isinstance(out.get("items"), list) else []
    return {r["id"]: r["folder"] for r in rows
            if isinstance(r, dict) and r.get("id") in ids and r.get("folder") in names}


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
    folders: list[dict] | None = None,
) -> dict:
    """`folders`: the user's folders ({name, description}) when Claude should pick one (`folder` in the answer: a
    folder's name, or NO_FOLDER)."""
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
    properties, required = dict(ENRICH_SCHEMA["properties"]), list(ENRICH_SCHEMA["required"])
    if perso:
        properties["category"] = {"type": "string", "enum": list(CATEGORIES)}
        required.append("category")
    if folders:
        properties["folder"] = _folder_property(folders)
        required.append("folder")
    second = get_settings().second_language
    if second:
        properties["translation"] = TRANSLATION_SCHEMA
        required.append("translation")
    request = dict(
        system=_enrich_system() + (_translation_rule(second) if second else "") + (_perso_rules() if perso else "")
        + (_folder_rules(folders) if folders else ""),
        content=prompt,
        tool_name="save_card",
        tool_description="Enregistre la fiche de l'élément dans la knowledge base.",
        schema={**ENRICH_SCHEMA, "properties": properties, "required": required},
        max_tokens=6500 if second else 3500,
    )
    out = call_tool(**request)
    if _malformed(out):
        # the tool call went wrong (seen once: every list sent as one "<item>…</item>" string): one more try
        log.warning("Fiche mal formée (%s), nouvel essai", ", ".join(_malformed(out)))
        again = call_tool(**request)
        if len(_malformed(again)) < len(_malformed(out)):
            out = again
    out["key_points"] = _text_list(out.get("key_points"))
    out["use_cases"] = _text_list(out.get("use_cases"))
    out["tags"] = _normalize_tags(_text_list(out.get("tags")))
    out["entities"] = _dict_list(out.get("entities"), "name")
    out["action_items"] = _dict_list(out.get("action_items"), "text")
    names = {f["name"] for f in folders or []}
    out["folder"] = out.get("folder") if out.get("folder") in names else None
    out["translations"] = {second: _clean_translation(out.pop("translation", None))} if second else {}
    if second and not out["translations"][second]:
        out["translations"] = {}
    return out


_CARD_LISTS = ("key_points", "use_cases", "tags", "entities", "action_items")


def _malformed(out: dict) -> list[str]:
    """The list fields of a card that didn't come back as lists."""
    return [k for k in _CARD_LISTS if not isinstance(out.get(k), list)]


def _text_list(value: Any) -> list[str]:
    """A list of strings from what the model sent. The API doesn't enforce the schema: a list may come back as one
    string, as JSON, as "<item>…</item>" tags or one per line."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
    if not isinstance(value, str):
        return []
    if value.lstrip().startswith("["):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return _text_list(parsed)
    tagged = [m.strip() for m in re.findall(r"<item>(.*?)</item>", value, re.S) if m.strip()]
    if tagged:
        return tagged
    lines = (re.sub(r"^\s*[-*•]\s*", "", line).strip() for line in value.splitlines())
    return [line for line in lines if line and not re.fullmatch(r"</?\w+>", line)]


def _dict_list(value: Any, key: str) -> list[dict]:
    """Objects that have the field `key` (entities need a name, actions a text)."""
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, dict) and isinstance(x.get(key), str) and x[key].strip()]


def _clean_translation(raw) -> dict:
    """The API doesn't enforce the schema: keep well-formed fields only."""
    if not isinstance(raw, dict):
        return {}
    out = {k: str(raw[k]).strip() for k in ("title", "summary") if isinstance(raw.get(k), str) and raw[k].strip()}
    for k in ("key_points", "use_cases"):
        if k in raw:
            out[k] = _text_list(raw[k])
    return {k: v for k, v in out.items() if v}


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
        max_tokens=4000,
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
    model = get_settings().enrich_model
    thinking = _thinking(model)
    resp = client().messages.create(model=model, max_tokens=_room(model, 2000, thinking),
                                    messages=[{"role": "user", "content": content}], **thinking)
    costs.record_claude(model, getattr(resp, "usage", None), "video_frames")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def transcribe_pdf(data: bytes, first_page: int = 1) -> str:
    """Pour les PDF scannés : Claude lit un lot de pages (≈10) et transcrit le texte (en streaming : pas de timeout)."""
    model = get_settings().enrich_model
    thinking = _thinking(model)
    with client().messages.stream(
        model=model,
        max_tokens=_room(model, 20000, thinking),
        **thinking,
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
        resp = stream.get_final_message()
    costs.record_claude(model, getattr(resp, "usage", None), "pdf_ocr")
    return "".join(b.text for b in resp.content if b.type == "text")


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
        max_tokens=2000,
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
            max_tokens=200,
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
        max_tokens=1000,
    )


# ---------------------------------------------------------------------------
# Wiki (app/wiki.py): pages written and kept up to date by Claude
# ---------------------------------------------------------------------------

WIKI_SECTIONS = ["personal", "research", "reading", "business", "competitive"]
WIKI_KINDS = ["theme", "entity", "book", "synthesis"]

_WIKI_PAGE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "section": {"type": "string", "enum": WIKI_SECTIONS},
        "kind": {"type": "string", "enum": WIKI_KINDS},
        "summary": {"type": "string", "description": "Une ligne : ce que couvre la page (pour l'index)."},
        "content": {"type": "string", "description": "La page entière, en Markdown."},
    },
    "required": ["title", "section", "kind", "summary", "content"],
}

WIKI_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "takeaways": {"type": "array", "items": {"type": "string"}},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "action": {"type": "string", "enum": ["update", "create"]},
                    "section": {"type": "string", "enum": WIKI_SECTIONS},
                    "kind": {"type": "string", "enum": WIKI_KINDS},
                    "why": {"type": "string"},
                },
                "required": ["title", "action", "section", "kind", "why"],
            },
        },
    },
    "required": ["takeaways", "pages"],
}

WIKI_WRITE_SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": _WIKI_PAGE}},
    "required": ["pages"],
}

WIKI_LINT_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {**_WIKI_PAGE, "properties": {**_WIKI_PAGE["properties"],
                                                   "reason": {"type": "string"}},
                      "required": [*_WIKI_PAGE["required"], "reason"]},
        },
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"from": {"type": "string"}, "into": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["from", "into", "reason"],
            },
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"},
                               "kind": {"type": "string", "enum": ["question", "source", "action", "link"]}},
                "required": ["text", "kind"],
            },
        },
    },
    "required": ["pages", "merges", "suggestions"],
}

WIKI_PICK_SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": {"type": "string"}}},
    "required": ["pages"],
}

WIKI_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "section": {"type": "string", "enum": WIKI_SECTIONS},
        "summary": {"type": "string"},
    },
    "required": ["title", "section", "summary"],
}

_WIKI_TITLE_RULE = ("un titre court et canonique (« Andrej Karpathy », « Évaluation des LLM »), sans les caractères "
                    "/ \\ : * ? \" < > | # ^ [ ]")

_WIKI_WRITING = """Règles d'écriture d'une page :
- Écris en {lang}. Dense, factuel, bien structuré : un court paragraphe de synthèse en tête, puis des sections ## par
  sous-thème. Jamais de liste de sources ni de « Cet article dit… » : l'information est rangée par thème, comme dans un
  bon livre, quelle que soit la source d'où elle vient.
- Chaque fait tiré d'une source porte sa référence [src:xxxxxxxx] (fournie avec la source). Garde telles quelles les
  références déjà présentes.
- Quand une source contredit ou nuance ce que dit une page, ne l'écrase pas : écris les deux avec leurs références et
  signale-le par « **Contradiction** : … ». Date les affirmations quand la date compte.
- Relie les pages : [[Titre exact]] vers une autre page du wiki dès qu'un sujet y est traité (titres fournis). Pas de
  lien vers une page qui n'existe pas, sauf vers une page écrite dans la même passe.
- Section Personnel : fidèle à ses mots et à sa pensée, au tutoiement, sans jugement ni conseil non demandé.
- Une page reste lisible : au-delà d'environ 12 000 caractères, condense ce qui se répète et renvoie le détail d'un
  sous-thème vers sa propre page ([[lien]]) plutôt que d'allonger.
- summary : une ligne qui dit ce que couvre la page."""


def _wiki_system(intro: str, schema_text: str, rules: str) -> str:
    return f"{intro}\n\n{schema_text}\n\n{rules}"


WIKI_MAX_TOKENS = 24_000     # with structured outputs the request gets twice this (thinking included)


def wiki_budget(contents: list[str], per_page: int = 2500) -> int:
    """Output tokens for pages returned in full: what they hold now (about 3 characters a token) plus room for what
    gets added, within WIKI_MAX_TOKENS."""
    return min(WIKI_MAX_TOKENS, 2000 + sum(len(c or "") // 3 + per_page for c in contents))


def wiki_plan(*, source: str, index: str, schema_text: str, max_pages: int, model: str | None = None) -> dict:
    """Which pages a new item should enrich (existing ones, by their exact title) and which to create."""
    rules = f"""Règles :
- Range l'information par thème : une page = un thème, un concept, une méthode, une entité importante (personne,
  outil, entreprise) ou un livre. Jamais une page par source, jamais une page « résumé de l'article ».
- Enrichis de préférence une page existante plutôt que d'en créer une voisine. Crée une page seulement pour un sujet
  qui mérite la sienne et n'existe pas encore.
- 1 à {max_pages} pages ; une source courte (un tweet) en touche souvent une ou deux.
- Page existante : recopie son titre exact de l'index (action « update »). Nouvelle page (action « create ») :
  {_WIKI_TITLE_RULE}.
- section : la section où la page vit. kind : theme, entity, book ou synthesis.
- why : ce que la source apporte à cette page, en une phrase.
- takeaways : 2 à 5 points que la source apporte, une ligne chacun (pour le journal du wiki), en {lang_name()}.
- Si la source n'apporte rien de durable (lien mort, contenu vide, blague), renvoie une liste de pages vide."""
    return call_tool(
        system=_wiki_system(
            "Tu tiens le wiki personnel de l'utilisateur : des pages Markdown reliées entre elles, que tu écris et "
            "mets à jour toi-même à partir de ce qu'il sauvegarde dans sa knowledge base. Une source vient d'arriver : "
            "décide quelles pages elle doit enrichir.", schema_text, rules),
        content=f"<index>\n{index or '(wiki vide pour l’instant)'}\n</index>\n\n<source>\n{source}\n</source>",
        tool_name="plan_wiki_update",
        tool_description="Enregistre les pages à mettre à jour ou à créer.",
        schema=WIKI_PLAN_SCHEMA,
        model=model or get_settings().wiki_model,
        max_tokens=2500,
    )


def wiki_write(*, source: str, ref: str, pages: list[dict], titles: list[str], schema_text: str,
               model: str | None = None) -> dict:
    """The planned pages rewritten in full with the new source integrated. `pages`: title, section, kind, why and the
    current content ('' for a new page)."""
    blocks = []
    for p in pages:
        current = p.get("content") or "(nouvelle page : à écrire)"
        blocks.append(f'<page title="{p["title"]}" section="{p["section"]}" kind="{p["kind"]}">\n'
                      f"Ce que la source lui apporte : {p.get('why') or '-'}\n\n{current}\n</page>")
    content = (f'<source ref="[src:{ref}]">\n{source}\n</source>\n\n'
               f"<titres_du_wiki>\n{chr(10).join(titles) or '(aucun)'}\n</titres_du_wiki>\n\n"
               f"<pages_a_ecrire>\n{chr(10).join(blocks)}\n</pages_a_ecrire>")
    rules = (_WIKI_WRITING.format(lang=lang_name())
             + f"\n- La référence de la nouvelle source est [src:{ref}]."
             + "\n- Intègre la source là où elle a sa place : complète une section, ajoutes-en une, reformule la synthèse"
               " si elle change. Garde tout ce qui reste vrai ; ne supprime rien sans raison."
             + f"\n- Si une page cite déjà [src:{ref}] (la source a été modifiée puis renvoyée), mets à jour ce qu'elle"
               " en disait au lieu de le répéter."
             + "\n- Renvoie exactement les pages demandées, chacune en entier, avec son titre exact.")
    return call_tool(
        system=_wiki_system("Tu tiens le wiki personnel de l'utilisateur. Intègre la nouvelle source dans les pages "
                            "ci-dessous et renvoie chacune réécrite en entier.", schema_text, rules),
        content=content,
        tool_name="write_wiki_pages",
        tool_description="Enregistre les pages réécrites.",
        schema=WIKI_WRITE_SCHEMA,
        model=model or get_settings().wiki_model,
        max_tokens=wiki_budget([p.get("content") or "" for p in pages]),
    )


def wiki_lint(*, pages: list[dict], findings: str, titles: list[str], sources: str, schema_text: str,
              model: str | None = None) -> dict:
    """The nightly review of a batch of pages: corrected pages, merges, new pages, ideas for what to dig next."""
    blocks = []
    for p in pages:
        extra = ""
        if p.get("lost_update"):
            extra = f"\n<mise_a_jour_ecrasee>\n{p['lost_update']}\n</mise_a_jour_ecrasee>"
        who = "modifiée par toi (l'utilisateur)" if p.get("updated_by") == "user" else "écrite par Claude"
        blocks.append(f'<page title="{p["title"]}" section="{p["section"]}" kind="{p["kind"]}" '
                      f'maj="{str(p.get("updated_at") or "")[:10]}" auteur="{who}">\n{p["content"]}{extra}\n</page>')
    rules = (_WIKI_WRITING.format(lang=lang_name()) + """

À vérifier et corriger :
- Contradictions entre pages ou dans une page : rends-les explicites ; tranche quand une source plus récente le permet,
  en gardant la trace de l'ancienne affirmation et de ses références.
- Affirmations périmées (une source plus récente les dépasse) : mets-les à jour ou date-les.
- Doublons : deux pages sur le même sujet → merges (from = la page à fondre, into = celle qui reste). La page qui
  reste doit être renvoyée dans pages, réécrite avec le contenu des deux.
- Liens : ajoute les [[liens]] manquants entre pages liées ; un lien vers une page inexistante → crée la page si le
  sujet le mérite (dans pages, avec un titre nouveau), sinon retire le lien.
- Pages orphelines (aucun lien entrant) : relie-les depuis les pages concernées.
- Concept important cité dans plusieurs pages mais sans page : crée-la à partir de ce que disent les pages, avec
  leurs références.
- Page « modifiée par toi » : le texte de l'utilisateur fait foi. Si elle a une <mise_a_jour_ecrasee> (une mise à
  jour de Claude qu'il n'a pas vue avant de la remplacer), réintègre seulement les faits qu'elle apportait et qui
  manquent, sans revenir sur ce qu'il a écrit.

Règles :
- pages : uniquement les pages à changer ou à créer, réécrites en entier ; un titre existant = la page existante.
  Laisse les autres telles quelles. Ne change pas le titre d'une page existante (pour renommer : merges).
- reason : une phrase qui dit ce que tu as corrigé.
- suggestions : 3 à 6 pistes pour la suite, une ligne chacune : question à creuser (question), source à chercher
  (source), action concrète tirée de ses lectures (action), lien entre deux de ses sujets à explorer (link).
- S'il n'y a rien à corriger, renvoie des listes pages et merges vides.""")
    content = (f"<constats_automatiques>\n{findings or '(aucun)'}\n</constats_automatiques>\n\n"
               f"<tous_les_titres>\n{chr(10).join(titles)}\n</tous_les_titres>\n\n"
               f"<sources_citees>\n{sources or '(aucune)'}\n</sources_citees>\n\n"
               f"<pages>\n{chr(10).join(blocks)}\n</pages>")
    return call_tool(
        system=_wiki_system("Tu relis, comme chaque nuit, le wiki personnel de l'utilisateur pour qu'il reste juste et "
                            "cohérent. Corrige ce qui doit l'être, sans réécrire ce qui va bien.", schema_text, rules),
        content=content,
        tool_name="review_wiki",
        tool_description="Enregistre les corrections du wiki.",
        schema=WIKI_LINT_SCHEMA,
        model=model or get_settings().wiki_lint_model,
        max_tokens=wiki_budget([p.get("content") or "" for p in pages], per_page=300),
    )


def wiki_pick(*, question: str, index: str, limit: int = 8) -> list[str]:
    """The titles of the pages to read to answer a question (from the index only)."""
    out = call_tool(
        system=(f"Tu choisis, dans l'index du wiki personnel de l'utilisateur, les pages à lire pour répondre à sa "
                f"question : au plus {limit} titres exacts, les plus utiles d'abord. Liste vide si aucune ne convient."),
        content=f"<index>\n{index}\n</index>\n\nQuestion : {question}",
        tool_name="pick_pages",
        tool_description="Enregistre les pages à lire.",
        schema=WIKI_PICK_SCHEMA,
        max_tokens=600,
    )
    return _text_list(out.get("pages"))[:limit]


def wiki_file_answer(*, question: str, answer: str, index: str) -> dict:
    """Title, section and summary of a page made from an answer worth keeping."""
    return call_tool(
        system=(f"L'utilisateur garde une réponse dans son wiki personnel, comme une nouvelle page. Donne-lui "
                f"{_WIKI_TITLE_RULE}, différent des titres de l'index, la section où elle vit, et une ligne de résumé, "
                f"en {lang_name()}."),
        content=f"<index>\n{index or '(vide)'}\n</index>\n\nQuestion : {question}\n\nRéponse :\n{answer[:12000]}",
        tool_name="file_answer",
        tool_description="Enregistre le titre, la section et le résumé de la page.",
        schema=WIKI_ANSWER_SCHEMA,
        max_tokens=600,
    )
