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
BROWSER_GET = web._browser_get          # the real one (conftest swaps it for a fake in every test)
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


def test_a_blocked_page_is_read_from_the_wayback_machine(monkeypatch, clean_db):
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


def test_jina_reads_it_first(monkeypatch, clean_db):
    _fake_web(monkeypatch, {URL: (403, BOT_CHECK),
                            "https://r.jina.ai/": (200, {"data": {"content": PAGE_TEXT, "title": "5 tricks for Claude"}})})
    ex = web.extract(URL, URL)
    assert ex.metadata == {"via": "jina"} and ex.title == "5 tricks for Claude"


def test_nothing_reads_it_says_what_to_do(monkeypatch, clean_db):
    asked = _fake_web(monkeypatch, {URL: (403, BOT_CHECK), "https://r.jina.ai/": (451, "blocked"),
                                    "https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError, match="medium.com refuse l'accès aux serveurs \\(403\\).*téléphone") as err:
        web.extract(URL, URL)
    assert "Page inaccessible : Page inaccessible" not in str(err.value)
    assert sum(u.startswith("https://r.jina.ai/") for u in asked) == 1


def test_a_network_error_is_retried_later(monkeypatch, clean_db):
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


def test_sites_that_block_servers_are_remembered(monkeypatch, clean_db):
    other = "https://news.ex.org/2026/une-enquete"
    assert web.blocks_servers(URL) and web.blocks_servers("https://ex-blog.medium.com/post-1")    # known
    assert not web.blocks_servers(other)
    _fake_web(monkeypatch, {other: (403, BOT_CHECK), "https://r.jina.ai/": (451, "blocked"),
                            "https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError):
        web.extract(other, other)
    assert web.blocks_servers(other) and web.blocks_servers("https://www.news.ex.org/autre")      # learned


def _page_upload(client, item_id, html, **kw):
    return client.post(f"/api/items/{item_id}/page", files={"page": ("page.html", html, "text/html")},
                       headers=AUTH, **kw)


def test_the_phone_fetches_pages_the_server_cannot(client, monkeypatch):
    """The Shortcut says it can fetch the page (page_follows). For a site that refuses servers, the answer asks for
    it (page_wanted); the item waits for it instead of hitting the 403, then is read from what the phone got."""
    from app import db

    def server_must_not_try(url):
        raise AssertionError("the server should read the phone's copy")

    monkeypatch.setattr(extractors, "extract_url", server_must_not_try)
    r = client.post("/api/ingest", json={"url": URL, "note": "pour mes prompts", "page_follows": "1"}, headers=AUTH)
    body = r.json()
    assert body["page_wanted"] is True and body["page_url"] == URL and body["message"] == "Ajouté à ta KB ✓"
    item_id = body["id"]
    assert drain() == 0                                   # waiting for the page, not hitting the 403

    assert _page_upload(client, item_id, ARTICLE_HTML.encode()).json() == {"ok": True, "status": "pending"}
    drain()
    row = db.fetchone("select status, title, author, content, metadata from items where id = %s", (item_id,))
    assert row["status"] == "ready" and row["title"] == "Les agents RAG en production" and row["author"] == "Jane Doe"
    assert "fidélité des réponses" in row["content"] and row["metadata"]["via"] == "phone"

    # nothing asked for a site that reads fine, nor from the app (it can't fetch other sites), nor for a tweet
    for payload in ({"url": "https://blog.ex.com/long", "page_follows": "1"}, {"url": URL + "?from=app"},
                    {"url": "https://x.com/alice/status/777", "page_follows": "1"}):
        assert "page_wanted" not in client.post("/api/ingest", json=payload, headers=AUTH).json()

    # deleting the item deletes the phone's page too
    client.delete(f"/api/items/{item_id}", headers=AUTH)
    with pytest.raises(Exception):
        storage.download(f"pages/{item_id}.html")


def test_a_failed_link_shared_again_is_read_from_the_phone(client, monkeypatch):
    from app import db

    monkeypatch.setattr(extractors, "extract_url", _blocked)
    item_id = client.post("/api/ingest", json={"url": URL}, headers=AUTH).json()["id"]      # from the app: no phone
    drain()
    assert db.fetchone("select status from items where id = %s", (item_id,))["status"] == "error"

    # shared from the Medium app with a broken Get URLs: only the text, which holds the link
    again = client.post("/api/ingest", json={"url": "", "text": URL, "page_follows": "1"}, headers=AUTH).json()
    assert again["id"] == item_id and again["page_wanted"] is True and again["page_url"] == URL
    _page_upload(client, item_id, ARTICLE_HTML.encode())
    drain()
    assert db.fetchone("select status from items where id = %s", (item_id,))["status"] == "ready"


def test_a_phone_page_without_an_article_is_ignored(client, monkeypatch):
    from app import db

    article = Extracted(kind="article", title="Lu par le serveur", source_url=URL,
                        content="Le serveur a fini par lire cette page en entier. " * 40)
    monkeypatch.setattr(extractors, "extract_url", lambda url: article)
    item_id = client.post("/api/ingest", json={"url": URL, "page_follows": "1"}, headers=AUTH).json()["id"]
    _page_upload(client, item_id, BOT_CHECK.encode())
    drain()
    assert db.fetchone("select content from items where id = %s", (item_id,))["content"] == article.content


def test_page_upload_errors(client):
    item_id = client.post("/api/ingest", json={"url": URL, "page_follows": "1"}, headers=AUTH).json()["id"]
    assert _page_upload(client, "pas-un-id", b"<html></html>").status_code == 404
    assert _page_upload(client, "00000000-0000-0000-0000-000000000000", b"<html></html>").status_code == 404
    assert _page_upload(client, item_id, b"").status_code == 400
    assert client.post(f"/api/items/{item_id}/page", content=b"<html></html>").status_code == 401
    raw = client.post(f"/api/items/{item_id}/page", content=ARTICLE_HTML.encode(),
                      headers={**AUTH, "Content-Type": "text/html"})
    assert raw.json() == {"ok": True, "status": "pending"}


def test_read_as_a_browser_before_anything_else(monkeypatch, clean_db):
    """A 403 often answers Python's TLS fingerprint, not the address: the same request as a browser gets the page."""
    asked = _fake_web(monkeypatch, {URL: (403, BOT_CHECK)})
    monkeypatch.setattr(web, "_browser_get", lambda url: ARTICLE_HTML.encode() if url == URL else None)
    ex = web.extract(URL, URL)
    assert ex.metadata["via"] == "browser" and ex.title == "Les agents RAG en production"
    assert not any(u.startswith("https://r.jina.ai/") for u in asked)            # no need for Jina

    monkeypatch.setattr(web, "_browser_get", lambda url: BOT_CHECK.encode())    # a bot check doesn't count
    _fake_web(monkeypatch, {URL: (403, BOT_CHECK), "https://r.jina.ai/": (200, {"data": {"content": PAGE_TEXT}})})
    assert web.extract(URL, URL).metadata == {"via": "jina"}


def test_the_error_says_the_phone_page_had_no_article(client, monkeypatch):
    from app import db

    monkeypatch.setattr(extractors, "extract_url", _blocked)
    item_id = client.post("/api/ingest", json={"url": URL, "page_follows": "1"}, headers=AUTH).json()["id"]
    _page_upload(client, item_id, BOT_CHECK.encode())
    drain()
    row = db.fetchone("select status, error from items where id = %s", (item_id,))
    assert row["status"] == "error" and row["error"].startswith("medium.com refuse l'accès aux serveurs (403).")
    assert "La page envoyée par ton téléphone est bien arrivée, mais sans l'article" in row["error"]


def test_browser_profiles_are_tried_in_turn(monkeypatch):
    from types import SimpleNamespace

    from curl_cffi import requests as browser

    tried = []

    def get(url, impersonate, **kw):
        tried.append(impersonate)
        if impersonate == "chrome":
            return SimpleNamespace(status_code=403, headers={"content-type": "text/html"}, content=BOT_CHECK.encode())
        return SimpleNamespace(status_code=200, headers={"content-type": "text/html; charset=utf-8"},
                               content=ARTICLE_HTML.encode())

    monkeypatch.setattr(browser, "get", get)
    assert BROWSER_GET(URL) == ARTICLE_HTML.encode() and tried == ["chrome", "safari"]
    monkeypatch.setattr(browser, "get", lambda url, impersonate, **kw: (_ for _ in ()).throw(OSError("réseau")))
    assert BROWSER_GET(URL) is None
