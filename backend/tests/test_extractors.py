import io

import pytest

from app.extractors import document, extract_file, pdf, twitter, web

from .pdfgen import make_pdf

ARTICLE_HTML = """<html><head><title>Les agents RAG en production</title>
<meta name="author" content="Jane Doe"><meta property="og:image" content="https://ex.com/cover.png">
<meta property="article:published_time" content="2026-05-04T10:00:00Z"><meta property="og:site_name" content="Le Blog IA">
</head><body><nav>Menu Accueil Contact</nav><article><h1>Les agents RAG en production</h1>
<p>{p1}</p><h2>Évaluation</h2><p>{p2}</p></article><footer>© 2026</footer></body></html>""".format(
    p1="Déployer un agent RAG demande une évaluation continue et des jeux de tests réalistes. " * 8,
    p2="Il faut mesurer la précision du retrieval, la fidélité des réponses et la latence. " * 8,
)


def test_web_article_extraction():
    ex = web.extract_html(ARTICLE_HTML.encode(), "https://blog.ex.com/rag", "https://blog.ex.com/rag")
    assert ex.kind == "article"
    assert ex.title == "Les agents RAG en production"
    assert ex.author == "Jane Doe"
    assert ex.published_at.year == 2026
    assert ex.thumbnail_url == "https://ex.com/cover.png"
    assert "fidélité des réponses" in ex.content
    assert "Menu Accueil" not in ex.content


def _make_pdf(text_pages: list[str]) -> bytes:
    return make_pdf(text_pages, title="Rapport sur les embeddings", author="Labo X", created="D:20250312120000")


def test_pdf_text_and_thumbnail():
    data = _make_pdf(["Les embeddings Voyage sont multilingues et performants pour le retrieval. " * 3,
                      "Deuxieme page : conclusions et recommandations pratiques pour le RAG. " * 3])
    ex = pdf.extract_bytes(data, "rapport.pdf")
    assert ex.kind == "pdf"
    assert ex.title == "Rapport sur les embeddings"
    assert "[p. 2]" in ex.content
    assert ex.metadata["pages"] == 2 and ex.metadata["extraction"] == "text"
    assert ex.thumbnail_bytes and ex.thumbnail_bytes[:4] == b"\x89PNG"
    assert ex.published_at.year == 2025


def test_scanned_pdf_uses_claude(fake_llm):
    data = _make_pdf([""] * 12)
    ex = pdf.extract_bytes(data, "scan.pdf")
    assert ex.metadata["extraction"] == "claude-ocr"
    assert "[p. 1]\nTexte OCR" in ex.content and "[p. 11]\nTexte OCR" in ex.content   # deux lots de 10 pages


def test_unreadable_pdf_is_permanent_error():
    from app.extractors import ExtractionError

    with pytest.raises(ExtractionError):
        pdf.extract_bytes(b"%PDF-1.4 pas vraiment un pdf", "casse.pdf")


def test_documents_docx_xlsx_md():
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["outil", "usage"])
    wb.active.append(["pgvector", "recherche vectorielle"])
    buf = io.BytesIO()
    wb.save(buf)
    ex = document.extract_bytes(buf.getvalue(), "outils.xlsx", None)
    assert "pgvector" in ex.content

    ex = document.extract_bytes("# Notes\n\nIdée de projet".encode(), "notes.md", "text/markdown")
    assert ex.title == "notes" and "Idée de projet" in ex.content


def test_extract_file_routes_images(fake_llm):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    ex = extract_file(buf.getvalue(), "capture.png", "image/png")
    assert ex.kind == "image"
    assert "Les agents RAG changent tout" in ex.content
    assert ex.author == "@someone"


# ---------------------------------------------------------------------------
# Twitter : API officielle simulée
# ---------------------------------------------------------------------------

USERS = [{"id": "u1", "username": "alice", "name": "Alice", "profile_image_url": "https://pbs/alice.jpg"},
         {"id": "u2", "username": "bob", "name": "Bob"}]
TWEETS = {
    "100": {"id": "100", "author_id": "u2", "text": "Question de Bob sur les agents ?", "conversation_id": "100",
            "created_at": "2024-01-01T09:00:00.000Z"},
    "101": {"id": "101", "author_id": "u1", "text": "1/ Thread sur les agents https://t.co/abc",
            "conversation_id": "100", "created_at": "2024-01-01T10:00:00.000Z",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
            "entities": {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://blog.ex.com/agents"}]}},
    "102": {"id": "102", "author_id": "u1", "text": "2/ tronqué…", "conversation_id": "100",
            "created_at": "2024-01-01T10:01:00.000Z",
            "note_tweet": {"text": "2/ La suite complète du thread, très longue, avec tous les détails."},
            "referenced_tweets": [{"type": "replied_to", "id": "101"}, {"type": "quoted", "id": "200"}],
            "public_metrics": {"like_count": 42}},
    "200": {"id": "200", "author_id": "u2", "text": "Tweet cité de Bob", "conversation_id": "200"},
}


class FakeX:
    def __init__(self, token):
        self.tweets, self.users, self.media, self.reads = {}, {}, {}, 0
        self.searched = False

    def lookup(self, ids):
        for i in ids:
            t = TWEETS[i]
            self.tweets[i] = t
            for r in t.get("referenced_tweets", []):
                self.tweets[r["id"]] = TWEETS[r["id"]]
            self.reads += 1
        for u in USERS:
            self.users[u["id"]] = u

    def search_conversation(self, conversation_id, username):
        self.searched = True


def test_tweet_thread_official_api(monkeypatch, fake_llm):
    monkeypatch.setattr(twitter, "XClient", FakeX)
    ex = twitter.extract("102", "alice")
    assert ex.kind == "tweet"
    assert ex.source_url == "https://x.com/alice/status/102"
    assert ex.author == "Alice (@alice)"
    assert "(1/2) 1/ Thread sur les agents https://blog.ex.com/agents" in ex.content
    assert "La suite complète du thread" in ex.content          # note_tweet préféré au texte tronqué
    assert "En réponse à @bob" in ex.content                      # contexte de la conversation
    assert "Tweet cité — @bob : Tweet cité de Bob" in ex.content
    assert [t["id"] for t in ex.metadata["thread"]] == ["101", "102"]
    assert ex.metadata["metrics"]["like_count"] == 42
    assert ex.metadata["quoted"]["url"] == "https://x.com/bob/status/200"


def test_article_blocks_to_markdown():
    md = twitter._blocks_to_md([{"type": "header-one", "text": "Titre"}, {"type": "unstyled", "text": "Corps"},
                                {"type": "unordered-list-item", "text": "point"}])
    assert md == "# Titre\n\nCorps\n\n- point"
    text, title = twitter._article_text({"article": {"title": "T", "plain_text": "x" * 300}})
    assert title == "T" and len(text) == 300
