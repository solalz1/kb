"""Two languages: English tags, a second-language card for every item, answers in the app's language, and a
Supabase URL pasted with its /rest/v1/ suffix."""

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.config import Settings, get_settings

from .conftest import drain
from .test_e2e import AUTH


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("pasted", [
    "https://abc.supabase.co/rest/v1/", " https://abc.supabase.co/ ", "https://abc.supabase.co/storage/v1",
    "https://abc.supabase.co",
])
def test_supabase_url_keeps_only_the_project(pasted):
    assert Settings(supabase_url=pasted).supabase_url == "https://abc.supabase.co"


def test_second_language_defaults():
    assert Settings(kb_language="fr").second_language == "en"
    assert Settings(kb_language="en").second_language == "fr"
    assert Settings(kb_language="fr", kb_second_language="none").second_language is None
    assert Settings(kb_language="fr", kb_second_language="fr").second_language is None


def test_enrich_asks_for_english_tags_and_a_translation(monkeypatch):
    seen = {}

    def call_tool(**kw):
        seen.update(kw)
        return {"title": "Classement Terre ou Eau", "summary": "Résumé.", "key_points": ["Un point"], "tags": ["#LLM Eval"],
                "entities": [], "use_cases": [], "action_items": [], "genre": "thread", "language": "en",
                "translation": {"title": "Land or Water ranking", "summary": "Summary.", "key_points": ["One point", ""],
                                "use_cases": "not a list"}}

    monkeypatch.setattr(llm, "call_tool", call_tool)
    out = llm.enrich(kind="tweet", title=None, author=None, source_url=None, published_at=None, content="x",
                     user_note=None, existing_tags=[])
    assert "EN ANGLAIS" in seen["system"] and "anglais" in seen["system"]
    assert "translation" in seen["schema"]["required"]
    assert out["tags"] == ["llm-eval"]
    assert out["translations"] == {"en": {"title": "Land or Water ranking", "summary": "Summary.",
                                          "key_points": ["One point"]}}

    monkeypatch.setattr(get_settings(), "kb_second_language", "none")
    seen.clear()
    out = llm.enrich(kind="tweet", title=None, author=None, source_url=None, published_at=None, content="x",
                     user_note=None, existing_tags=[])
    assert "translation" not in seen["schema"]["properties"] and out["translations"] == {}


def test_items_carry_their_card_in_both_languages(client):
    from app import db

    r = client.post("/api/ingest", json={"text": "Une évaluation des modèles sur la géographie terrestre"}, headers=AUTH)
    item_id = r.json()["id"]
    drain()
    row = db.fetchone("select title, translations from items where id = %s", (item_id,))
    en = row["translations"]["en"]
    assert en["summary"].startswith("Summary of the note") and en["key_points"] == ["Point A (en)", "Point B (en)"]
    assert en["title"].startswith("Generated title")

    # the card that gets embedded holds both summaries
    card = db.fetchone("select content from chunks where item_id = %s and chunk_index = -1", (item_id,))["content"]
    assert "Summary (en): Summary of the note" in card and "Résumé : Résumé de note" in card

    listed = client.get("/api/items", headers=AUTH).json()["items"][0]
    assert listed["translations"]["en"]["title"] == en["title"]
    assert client.get(f"/api/items/{item_id}", headers=AUTH).json()["translations"]["en"]["use_cases"] == ["Useful to test the KB"]

    # a title written by hand is the same in every language
    client.patch(f"/api/items/{item_id}", json={"title": "Mon titre"}, headers=AUTH)
    tr = db.fetchone("select translations from items where id = %s", (item_id,))["translations"]
    assert "title" not in tr["en"] and tr["en"]["summary"] == en["summary"]


def test_source_title_is_not_translated(client, monkeypatch):
    from app import db, extractors
    from app.extractors.base import Extracted

    monkeypatch.setattr(extractors, "extract_url", lambda url: Extracted(
        kind="article", title="Le titre de l'article", source_url=url, content="Un article. " * 50))
    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/article"}, headers=AUTH)
    drain()
    row = db.fetchone("select title, translations from items where id = %s", (r.json()["id"],))
    assert row["title"] == "Le titre de l'article" and "title" not in row["translations"]["en"]


def test_chat_answers_in_the_app_language(client, fake_llm):
    client.post("/api/ingest", json={"text": "Une note sur les agents"}, headers=AUTH)
    drain()
    for lang, expected in (("en", "Réponds en anglais"), ("fr", "Réponds en français"), (None, "Réponds en français")):
        body = {"messages": [{"role": "user", "content": "Que sais-je des agents ?"}], **({"lang": lang} if lang else {})}
        client.post("/api/chat", json=body, headers=AUTH)
        assert expected in fake_llm["stream"][-1]["system"]
    sources = [l for l in client.post("/api/chat", json={"messages": [{"role": "user", "content": "agents"}]},
                                      headers=AUTH).text.splitlines() if '"sources"' in l]
    assert '"translations": {"en": {"title": "Generated title' in sources[0]
