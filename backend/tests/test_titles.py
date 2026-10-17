"""Every card gets a title that says what it is about: a source's real headline word for word, else one from Claude
(a LinkedIn post whose page "title" is its first words, a file name, a headline with the site's name after it)."""

import pytest

from app import db, extractors, llm, pipeline
from app.extractors.base import Extracted
from app.titles import looks_like_title

from .conftest import drain
from .test_llm import FakeClaude

POST_TITLE = ("If you want to get good at AI, here's my playbook: 1. Use one coding agent for everything. "
              "2. Every single task... | Jane Doe")


@pytest.mark.parametrize("title", [
    POST_TITLE,
    'Jane Doe on X: "Agents need context, not more tokens"',
    "Post de Jane Doe sur LinkedIn",
    "Building reliable agents | Medium",
    "Mon intervention au salon - YouTube",
    "compte-rendu-final.pdf",
    "https://example.com/blog/agents",
    "LinkedIn",
    "Accueil",
    "A" * 120,
    "",
    None,
])
def test_what_is_not_a_title(title):
    assert not looks_like_title(title)


@pytest.mark.parametrize("title", [
    "Ce que j'ai appris en faisant tourner un RAG en production pendant un an",
    "Attention Is All You Need",
    "Évaluer les assistants de recherche : un protocole en trois niveaux",
    "exemple/vectordb-lite — une base vectorielle en un fichier",
    "GPT-5 - ce qui change pour les agents",
    "Node.js en 2026",
])
def test_real_headlines(title):
    assert looks_like_title(title)


def test_choose_title():
    claude = "Le protocole de Jane Doe pour progresser en IA"
    assert pipeline.choose_title("article", POST_TITLE, claude) == claude
    assert pipeline.choose_title("article", "Attention Is All You Need", "Un papier sur l'attention") \
        == "Attention Is All You Need"
    assert pipeline.choose_title("tweet", "Pourquoi les agents ratent", "Autre") == "Pourquoi les agents ratent"
    assert pipeline.choose_title("note", "Une idée", "Le titre de Claude") == "Le titre de Claude"   # notes: Claude's
    assert pipeline.choose_title("image", "capture.png", None) == "capture.png"      # nothing better: what there is
    assert pipeline.choose_title("article", None, None) is None


def _ingest(monkeypatch, page_title, **kw):
    monkeypatch.setattr(extractors, "extract_url", lambda url: Extracted(
        kind="article", title=page_title, source_url=url, author="Jane Doe", content="Un protocole en trois règles. " * 20))
    item_id = pipeline.ingest(url="https://www.linkedin.com/posts/janedoe_ai-activity-1", **kw)["id"]
    drain()
    return db.fetchone("select title, translations, status from items where id = %s", (item_id,))


def test_a_linkedin_post_gets_a_title(clean_db, fake_llm, monkeypatch):
    row = _ingest(monkeypatch, POST_TITLE)
    assert row["status"] == "ready"
    assert row["title"].startswith("Titre généré : Un protocole")                    # Claude's (faked)
    assert row["translations"]["en"]["title"].startswith("Generated title: Un protocole")


def test_a_real_headline_stays_word_for_word(clean_db, fake_llm, monkeypatch):
    row = _ingest(monkeypatch, "Trois règles pour progresser en IA")
    assert row["title"] == "Trois règles pour progresser en IA"
    assert "en" not in row["translations"] or "title" not in row["translations"]["en"]   # the same in every language


def test_a_title_written_by_hand_wins(clean_db, fake_llm, monkeypatch):
    row = _ingest(monkeypatch, POST_TITLE, title="Ma note sur le protocole")
    assert row["title"] == "Ma note sur le protocole"


def test_cards_saved_with_a_post_as_title_are_fixed_once(clean_db, monkeypatch):
    def insert(kind, title, metadata=None):
        return db.fetchone(
            """insert into items (kind, title, summary, status, metadata, translations)
               values (%s, %s, 'Un protocole en trois règles pour progresser en IA.', 'ready', %s, %s)
               returning id::text""",
            (kind, title, db.jsonb(metadata or {}),
             db.jsonb({"en": {"summary": "Three rules.", "key_points": [], "use_cases": []}})))["id"]

    post = insert("article", POST_TITLE)
    tweet = insert("tweet", 'Jane Doe on X: "Agents need context"')
    good = insert("article", "Trois règles pour progresser en IA")
    mine = insert("article", POST_TITLE, {"manual_title": True})
    asked = []

    def retitle(**kw):
        asked.append(kw["title"])
        return {"title": "Le protocole de Jane Doe pour progresser en IA",
                "translation": "Jane Doe's protocol for getting good at AI"}

    monkeypatch.setattr(llm, "retitle", retitle)
    assert pipeline.fix_titles() == 2
    rows = {r["id"]: r for r in db.fetchall("select id::text, title, translations from items")}
    assert rows[post]["title"] == rows[tweet]["title"] == "Le protocole de Jane Doe pour progresser en IA"
    assert rows[post]["translations"]["en"] == {"summary": "Three rules.", "key_points": [], "use_cases": [],
                                                "title": "Jane Doe's protocol for getting good at AI"}
    assert rows[good]["title"] == "Trois règles pour progresser en IA"
    assert rows[mine]["title"] == POST_TITLE                       # written by hand: never touched
    assert sorted(asked) == sorted([POST_TITLE, 'Jane Doe on X: "Agents need context"'])
    # once only
    assert pipeline.fix_titles() == 0
    assert len(asked) == 2


def test_fixing_titles_is_tried_again_when_claude_fails(clean_db, monkeypatch):
    db.execute("""insert into items (kind, title, status) values ('article', %s, 'ready')""", (POST_TITLE,))

    def down(**kw):
        raise RuntimeError("API unreachable")

    monkeypatch.setattr(llm, "retitle", down)
    pipeline.startup_titles()                                      # logged, not raised
    assert not db.fetchone("select 1 from kb_settings where key = %s", (pipeline.TITLES_FIXED,))
    monkeypatch.setattr(llm, "retitle", lambda **kw: {"title": "Le protocole de Jane Doe"})
    pipeline.startup_titles()
    assert db.fetchone("select title from items")["title"] == "Le protocole de Jane Doe"


@pytest.fixture
def claude_says(monkeypatch, clean_db):
    def install(answer):
        fake = FakeClaude(answer)
        monkeypatch.setattr(llm, "_NO_FORCED_TOOL", set())
        monkeypatch.setattr(llm, "client", lambda: fake)
        return fake
    return install


CARD = {"summary": "Un protocole en trois règles.", "key_points": ["Un agent pour tout"], "tags": ["ai"],
        "entities": [], "use_cases": ["Utile pour progresser"], "action_items": [], "genre": "opinion",
        "language": "en", "translation": {"title": "Jane Doe's protocol for AI", "summary": "Three rules.",
                                          "key_points": ["One agent"], "use_cases": ["Useful to progress"]}}


def test_claude_is_told_how_to_title_any_source(claude_says):
    fake = claude_says({**CARD, "title": "« Le protocole de Jane Doe pour progresser en IA. »"})
    out = llm.enrich(kind="article", title=POST_TITLE, author="Jane Doe", source_url="https://www.linkedin.com/posts/x",
                     published_at=None, content="Un protocole en trois règles.", user_note=None, existing_tags=[])
    assert out["title"] == "Le protocole de Jane Doe pour progresser en IA"        # one clean line
    call = fake.calls[0]
    assert "pas forcément un vrai titre" in str(call["messages"])                  # this page's "title" isn't one
    assert "LinkedIn" in call["system"] and "mot pour mot" in call["system"]       # the rule, for every source

    fake = claude_says({**CARD, "title": "Trois règles pour progresser en IA"})
    llm.enrich(kind="article", title="Trois règles pour progresser en IA", author=None, source_url=None,
               published_at=None, content="…", user_note=None, existing_tags=[])
    assert "pas forcément" not in str(fake.calls[0]["messages"])


def test_retitle(claude_says):
    fake = claude_says({"title": "“Le protocole de Jane Doe pour progresser en IA.”",
                        "translation": "Jane Doe's protocol for getting good at AI"})
    out = llm.retitle(kind="article", title=POST_TITLE, summary="Un protocole en trois règles.", author="Jane Doe",
                      source_url="https://www.linkedin.com/posts/x")
    assert out == {"title": "Le protocole de Jane Doe pour progresser en IA",
                   "translation": "Jane Doe's protocol for getting good at AI"}
    sent = str(fake.calls[0]["messages"])
    assert POST_TITLE in sent and "Un protocole en trois règles." in sent
