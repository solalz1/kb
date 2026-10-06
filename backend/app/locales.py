"""What the KB writes outside the app (Notion copy, Markdown export) in French or English.

An item keeps its card in the main language (KB_LANGUAGE) in its columns, and in the second language in
`items.translations`; `localized()` returns the item as a reader of the chosen language should see it.
"""

from __future__ import annotations

from .config import get_settings
from .taxonomy import CATEGORIES, KIND_LABELS, SPACE_LABELS

KIND_LABELS_EN = {
    "tweet": "Tweet", "article": "Article", "youtube": "YouTube video", "video": "Video", "audio": "Audio",
    "pdf": "PDF", "image": "Image", "document": "Document", "note": "Note", "repo": "GitHub repository", "paper": "Paper",
}
SPACE_LABELS_EN = {"main": "Feed", "perso": "Personal"}
CATEGORY_LABELS_EN = {
    "principe": "Principle", "valeur": "Value", "lecon": "Lesson", "objectif": "Goal", "habitude": "Habit",
    "reflexion": "Reflection", "journal": "Journal", "citation": "Quote", "ressource": "Resource",
}

CATEGORY_PLURALS_EN = {
    "principe": "Principles", "valeur": "Values", "lecon": "Lessons", "objectif": "Goals", "habitude": "Habits",
    "reflexion": "Reflections", "journal": "Journal", "citation": "Quotes", "ressource": "Resources",
}

TEXT = {
    "fr": {
        "why": "Pourquoi je l'ai gardé", "summary": "Résumé", "key_points": "Points clés", "use_cases": "Utile pour",
        "people": "Personnes, outils, concepts", "entities": "Entités", "related": "Liés", "content": "Contenu",
        "untitled": "sans titre", "truncated": "… contenu tronqué : {n} caractères de plus dans la fiche KB",
        "note_truncated": "… note tronquée dans Notion : version complète dans l'app et dans l'export",
        "not_ready": "(Fiche pas encore générée : traitement en erreur dans l'app.)",
        "empty": "(vide)", "item": "élément", "original_file": "Fichier d'origine", "file": "fichier",
        "feed_folder": "Veille", "perso_folder": "Perso", "other": "Divers",
    },
    "en": {
        "why": "Why I kept it", "summary": "Summary", "key_points": "Key points", "use_cases": "Useful for",
        "people": "People, tools, concepts", "entities": "Entities", "related": "Related", "content": "Content",
        "untitled": "untitled", "truncated": "… content cut: {n} more characters in the KB card",
        "note_truncated": "… note cut in Notion: the full version is in the app and in the export",
        "not_ready": "(Card not generated yet: processing failed in the app.)",
        "empty": "(empty)", "item": "item", "original_file": "Original file", "file": "file",
        "feed_folder": "Feed", "perso_folder": "Personal", "other": "Other",
    },
}

# Notion column names, by language (keys are ours, values what Notion shows)
NOTION_PROPS = {
    "fr": {"name": "Nom", "space": "Espace", "category": "Catégorie", "kind": "Type", "tags": "Tags",
           "summary": "Résumé", "source": "Source", "author": "Auteur", "published": "Publié", "added": "Ajouté",
           "archived": "Archivé", "kb_url": "Fiche KB", "kb_id": "ID KB"},
    "en": {"name": "Name", "space": "Space", "category": "Category", "kind": "Type", "tags": "Tags",
           "summary": "Summary", "source": "Source", "author": "Author", "published": "Published", "added": "Added",
           "archived": "Archived", "kb_url": "KB card", "kb_id": "KB ID"},
}


def available() -> list[str]:
    """Languages the cards exist in: the main one, then the second one."""
    s = get_settings()
    return [s.kb_language] + ([s.second_language] if s.second_language else [])


def normalize(lang: str | None) -> str:
    langs = available()
    return lang if lang in langs else langs[0]


def text(lang: str) -> dict[str, str]:
    return TEXT.get(lang) or TEXT["en"]


def _labels(lang: str) -> tuple[dict, dict, dict]:
    if lang == "fr":
        return KIND_LABELS, SPACE_LABELS, {k: v[0] for k, v in CATEGORIES.items()}
    return KIND_LABELS_EN, SPACE_LABELS_EN, CATEGORY_LABELS_EN


def kind_labels(lang: str) -> dict[str, str]:
    return _labels(lang)[0]


def space_labels(lang: str) -> dict[str, str]:
    return _labels(lang)[1]


def category_labels(lang: str) -> dict[str, str]:
    return _labels(lang)[2]


def category_plurals(lang: str) -> dict[str, str]:
    return {k: v[1] for k, v in CATEGORIES.items()} if lang == "fr" else CATEGORY_PLURALS_EN


def notion_props(lang: str) -> dict[str, str]:
    return NOTION_PROPS.get(lang) or NOTION_PROPS["en"]


def localized(it: dict, lang: str) -> dict:
    """The item with its title, summary, key points and use cases in `lang`, when the KB has them."""
    tr = (it.get("translations") or {}).get(lang) if lang != get_settings().kb_language else None
    if not tr:
        return it
    out = dict(it)
    for key in ("title", "summary", "key_points", "use_cases"):
        if tr.get(key):
            out[key] = tr[key]
    return out
