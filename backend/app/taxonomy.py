"""Vocabulaire partagé : types d'éléments, espaces (Veille / Perso) et catégories de l'espace Perso."""

from __future__ import annotations

import unicodedata

KIND_LABELS = {
    "tweet": "Tweet", "article": "Article", "youtube": "Vidéo YouTube", "video": "Vidéo", "audio": "Audio",
    "pdf": "PDF", "image": "Image", "document": "Document", "note": "Note", "repo": "Dépôt GitHub", "paper": "Paper",
}

# 'main' = la veille (ce que tu captures) ; 'perso' = développement personnel (tes principes, leçons, objectifs…)
SPACES = ("main", "perso")
SPACE_LABELS = {"main": "Veille", "perso": "Perso"}

# clé -> (libellé, pluriel, description pour Claude)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "principe": ("Principe", "Principes", "règle de conduite ou de décision que l'utilisateur s'est fixée"),
    "valeur": ("Valeur", "Valeurs", "ce qui compte le plus pour lui, ce qu'il veut incarner"),
    "lecon": ("Leçon", "Leçons", "enseignement tiré d'une expérience vécue (réussite, erreur, échec)"),
    "objectif": ("Objectif", "Objectifs", "but qu'il poursuit, à court ou long terme"),
    "habitude": ("Habitude", "Habitudes", "routine ou pratique qu'il veut installer ou garder"),
    "reflexion": ("Réflexion", "Réflexions", "pensée, question ou idée sur lui-même ou sur la vie"),
    "journal": ("Journal", "Journal", "récit d'une journée ou d'un moment, ressenti du moment"),
    "citation": ("Citation", "Citations", "phrase d'un tiers qui l'inspire"),
    "ressource": ("Ressource", "Ressources", "livre, vidéo, article ou méthode de développement personnel"),
}
# La « charte » : toujours fournie en entier au mode Conseil
CHARTER_CATEGORIES = ("principe", "valeur")


def _ascii(word: str) -> str:
    return unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower().strip()


_ALIASES = {k: k for k in CATEGORIES}
for _key, (_label, _plural, _) in CATEGORIES.items():
    _ALIASES[_ascii(_label)] = _key
    _ALIASES[_ascii(_plural)] = _key
_ALIASES.update({"lecons": "lecon", "lesson": "lecon", "lessons": "lecon", "goal": "objectif", "goals": "objectif",
                 "value": "valeur", "values": "valeur", "principle": "principe", "principles": "principe",
                 "habit": "habitude", "habits": "habitude", "quote": "citation", "quotes": "citation"})

_SPACE_ALIASES = {"main": "main", "veille": "main", "perso": "perso", "personnel": "perso", "personal": "perso"}


def normalize_category(value: str | None) -> str | None:
    """« Leçons », « lecon », « principles »… → clé ; None si vide. ValueError si inconnue."""
    if value is None or not str(value).strip():
        return None
    key = _ALIASES.get(_ascii(str(value)))
    if not key:
        raise ValueError(f"Catégorie inconnue : {value} (au choix : {', '.join(CATEGORIES)})")
    return key


def category_from_word(word: str) -> str | None:
    return _ALIASES.get(_ascii(word))


def normalize_space(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    key = _SPACE_ALIASES.get(_ascii(str(value)))
    if not key:
        raise ValueError(f"Espace inconnu : {value} (au choix : veille, perso)")
    return key


def space_from_word(word: str) -> str | None:
    return _SPACE_ALIASES.get(_ascii(word))


def category_label(key: str | None) -> str | None:
    return CATEGORIES[key][0] if key in CATEGORIES else None
