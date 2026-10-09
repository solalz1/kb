"""Sites that refuse the server (Medium answers 403 to datacenter IPs): Jina Reader, then the Wayback Machine, then the
page the phone sent when it was shared from Safari. All HTTP is faked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import extractors, storage
from app.extractors import ExtractionError, Extracted, web

from .conftest import drain
from .test_e2e import AUTH
from .test_extractors import ARTICLE_HTML

URL = "https://medium.com/ex-publication/5-tricks-for-claude-1a2b3c4d5e6f"
BOT_CHECK = "<html><title>Just a moment...</title><body>Enable JavaScript and cookies to continue</body></html>"
PAGE_TEXT = ("5 tricks for Claude\nMember-only story\n" + "Write the instructions once and let Claude reuse them. " * 60)


def _fake_web(monkeypatch, routes: dict) -> list[str]:
    """web.http_client answering from `routes` ({url prefix: (status, body)}); returns the URLs asked for."""
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        for prefix, (status, body) in routes.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                if isinstance(body, dict):
                    return httpx.Response(status, json=body)
                return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    monkeypatch.setattr(web, "http_client", lambda **kw: httpx.Client(
        transport=httpx.MockTransport(handler), headers=kw.get("headers"), follow_redirects=True))
    return asked


WAYBACK = {"archived_snapshots": {"closest": {"available": True, "timestamp": "20261009120000",
                                             "url": f"http://web.archive.org/web/20261009120000/{URL}"}}}


def test_a_blocked_page_is_read_from_the_wayback_machine(monkeypatch):
    asked = _fake_web(monkeypatch, {
        URL: (403, BOT_CHECK),
        "https://r.jina.ai/": (200, {"data": {"content": BOT_CHECK, "title": "Just a moment..."}}),   # a bot check
        "https://archive.org/wayback/available": (200, WAYBACK),
        f"https://web.archive.org/web/20261009120000id_/{URL}": (200, ARTICLE_HTML),
    })
    ex = web.extract(URL, URL)
    assert ex.title == "Les agents RAG en production" and "fidélité des réponses" in ex.content
    assert ex.metadata["via"] == "wayback" and ex.metadata["archived_at"] == "20261009120000"
    assert sum(u.startswith("https://r.jina.ai/") for u in asked) == 1      # Jina asked once, not twice


def test_jina_reads_it_first(monkeypatch):
    _fake_web(monkeypatch, {URL: (403, BOT_CHECK),
                            "https://r.jina.ai/": (200, {"data": {"content": PAGE_TEXT, "title": "5 tricks for Claude"}})})
    ex = web.extract(URL, URL)
    assert ex.metadata == {"via": "jina"} and ex.title == "5 tricks for Claude"


def test_nothing_reads_it_says_what_to_do(monkeypatch):
    asked = _fake_web(monkeypatch, {URL: (403, BOT_CHECK), "https://r.jina.ai/": (451, "blocked"),
                                    "https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError, match="medium.com refuse l'accès aux serveurs \\(403\\).*Safari") as err:
        web.extract(URL, URL)
    assert "Page inaccessible : Page inaccessible" not in str(err.value)
    assert sum(u.startswith("https://r.jina.ai/") for u in asked) == 1


def test_a_network_error_is_retried_later(monkeypatch):
    _fake_web(monkeypatch, {URL: (0, httpx.ConnectError("connexion refusée")), "https://r.jina.ai/": (503, "down"),
                            "https://archive.org/wayback/available": (503, "down")})
    with pytest.raises(RuntimeError, match="Page inaccessible : connexion refusée"):
        web.extract(URL, URL)


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _blocked(url):
    raise ExtractionError("medium.com refuse l'accès aux serveurs (403).")


def test_the_page_sent_by_the_phone_is_used(client, monkeypatch):
    """Shared from Safari, the Shortcut sends the page's text with its link: when the site refuses the server, the
    card is written from what the phone showed."""
    from app import db

    monkeypatch.setattr(extractors, "extract_url", _blocked)
    r = client.post("/api/ingest", json={"url": URL, "text": PAGE_TEXT, "note": "pour mes prompts"}, headers=AUTH)
    item_id = r.json()["id"]
    meta = db.fetchone("select metadata, input_text from items where id = %s", (item_id,))
    assert meta["input_text"] is None and meta["metadata"]["page_path"] == f"pages/{item_id}.txt"
    drain()
    row = db.fetchone("select status, error, kind, content, site_name, metadata from items where id = %s", (item_id,))
    assert row["status"] == "ready" and row["error"] is None and row["kind"] == "article"
    assert row["content"].startswith("5 tricks for Claude") and row["site_name"] == "medium.com"
    assert row["metadata"]["via"] == "phone"

    # deleting the item deletes the page's text too
    assert client.delete(f"/api/items/{item_id}", headers=AUTH).json() == {"ok": True}
    with pytest.raises(Exception):
        storage.download(f"pages/{item_id}.txt")


def test_shared_again_from_safari_after_a_failure(client, monkeypatch):
    from app import db

    monkeypatch.setattr(extractors, "extract_url", _blocked)
    item_id = client.post("/api/ingest", json={"url": URL}, headers=AUTH).json()["id"]      # from the Medium app
    drain()
    row = db.fetchone("select status, error from items where id = %s", (item_id,))
    assert row["status"] == "error" and row["error"] == "medium.com refuse l'accès aux serveurs (403)."   # no type

    again = client.post("/api/ingest", json={"url": URL, "text": PAGE_TEXT}, headers=AUTH).json()
    assert again["items"] == [{"id": item_id, "status": "pending", "duplicate": True, "retried": True}]
    assert again["message"] == "Page reçue, je la relis ✓"
    drain()
    row = db.fetchone("select status, content from items where id = %s", (item_id,))
    assert row["status"] == "ready" and "Write the instructions once" in row["content"]


def test_a_paywall_preview_gives_way_to_the_full_page(client, monkeypatch):
    from app import db

    preview = Extracted(kind="article", title="5 tricks for Claude", source_url=URL, author="Jane Doe",
                        site_name="Medium", content="For almost two years, I used the same prompt. " * 12)
    monkeypatch.setattr(extractors, "extract_url", lambda url: preview)
    item_id = client.post("/api/ingest", json={"url": URL, "text": PAGE_TEXT}, headers=AUTH).json()["id"]
    drain()
    row = db.fetchone("select content, author, site_name from items where id = %s", (item_id,))
    assert "Write the instructions once" in row["content"] and row["author"] == "Jane Doe" and row["site_name"] == "Medium"

    # a page the server read in full keeps the server's clean version
    full = Extracted(kind="article", title="Un long article", source_url="https://blog.ex.com/long",
                     content="Un paragraphe propre et complet sur le sujet. " * 120)
    monkeypatch.setattr(extractors, "extract_url", lambda url: full)
    item_id = client.post("/api/ingest", json={"url": "https://blog.ex.com/long", "text": PAGE_TEXT},
                          headers=AUTH).json()["id"]
    drain()
    assert db.fetchone("select content from items where id = %s", (item_id,))["content"] == full.content
