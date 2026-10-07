"""Copie Notion : API Notion simulée (httpx.MockTransport), vraie base Postgres."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import extractors, notion
from app.config import get_settings
from app.extractors.base import Extracted

from .conftest import drain
from .test_e2e import AUTH

PARENT_URL = "https://www.notion.so/mon-espace/Ma-KB-0123456789abcdef0123456789abcdef?pvs=4"


class FakeNotion:
    """Imite les quelques routes utilisées : bases, pages, contenu Markdown, corbeille."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.databases = 0
        self.pages: dict[str, dict] = {}
        self.deleted: set[str] = set()
        self.source_gone = False
        self.rate_limit_next = False
        self.max_markdown: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path, body))
        assert request.headers["Notion-Version"] == notion.VERSION
        assert request.headers["Authorization"] == "Bearer ntn_test"
        if self.rate_limit_next:
            self.rate_limit_next = False
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited", "message": "slow"})
        if request.method == "POST" and path == "/databases":
            self.databases += 1
            self.source_gone = False
            n = self.databases
            return httpx.Response(200, json={"id": f"db{n}", "url": f"https://www.notion.so/db{n}",
                                             "data_sources": [{"id": f"ds{n}", "name": "Knowledge base"}]})
        if request.method == "POST" and path == "/pages":
            if self.source_gone or body["parent"]["data_source_id"] != f"ds{self.databases}":
                return httpx.Response(404, json={"code": "object_not_found", "message": "Could not find data source"})
            if self.max_markdown and len(body.get("markdown", "")) > self.max_markdown:
                return httpx.Response(400, json={"code": "validation_error", "message": "Content too large"})
            page_id = f"page{len(self.pages) + 1}"
            self.pages[page_id] = body
            return httpx.Response(200, json={"id": page_id})
        if request.method == "PATCH" and path.startswith("/pages/"):
            page_id = path.split("/")[2]
            if self.source_gone or page_id in self.deleted or page_id not in self.pages:
                return httpx.Response(404, json={"code": "object_not_found", "message": "Could not find page"})
            if path.endswith("/markdown"):
                self.pages[page_id]["markdown"] = body["replace_content"]["new_str"]
            else:
                self.pages[page_id].update(body)
            return httpx.Response(200, json={"id": page_id})
        return httpx.Response(400, json={"code": "invalid_request_url", "message": path})

    def made(self, method: str, prefix: str) -> list[dict]:
        return [b for m, p, b in self.calls if m == method and p.startswith(prefix)]


@pytest.fixture
def fake_notion(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "notion_token", "ntn_test")
    monkeypatch.setattr(s, "notion_parent_page_id", PARENT_URL)
    monkeypatch.setattr(s, "notion_spaces", "main,perso")
    fake = FakeNotion()
    monkeypatch.setattr(notion, "_client", httpx.Client(base_url=notion.API, transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(notion, "_sleep", lambda seconds: None)
    monkeypatch.setattr(notion, "MIN_INTERVAL", 0)
    return fake


@pytest.fixture
def client(clean_db, fake_llm, monkeypatch):
    monkeypatch.setattr(extractors, "extract_url", lambda url: Extracted(
        kind="article", title="Le stoïcisme au quotidien", source_url="https://blog.ex.com/stoicisme",
        author="Jane Doe", content="Distinguer ce qui dépend de nous de ce qui n'en dépend pas. " * 30))
    from app.main import app

    with TestClient(app) as c:
        yield c


def _setup(client):
    note = client.post("/api/notes", json={"title": "Honnêteté", "content": "Je dis la vérité, même quand elle coûte.",
                                           "category": "valeur", "tags": ["éthique"]}, headers=AUTH).json()["id"]
    article = client.post("/api/ingest", json={"url": "https://blog.ex.com/stoicisme", "note": "à relire"},
                          headers=AUTH).json()["id"]
    drain()
    return note, article


def test_parent_page_id_parsing(monkeypatch):
    s = get_settings()
    for raw, expected in [
        (PARENT_URL, "01234567-89ab-cdef-0123-456789abcdef"),
        ("0123456789ABCDEF0123456789ABCDEF", "01234567-89ab-cdef-0123-456789abcdef"),
        ("01234567-89ab-cdef-0123-456789abcdef", "01234567-89ab-cdef-0123-456789abcdef"),
        ("https://www.notion.so/Page-sans-id", None),
        ("", None),
    ]:
        monkeypatch.setattr(s, "notion_parent_page_id", raw)
        assert notion.parent_page_id() == expected, raw


def test_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "notion_token", "")
    st = client.get("/api/notion", headers=AUTH).json()
    assert st["configured"] is False and "NOTION_TOKEN" in st["missing"]
    assert client.post("/api/notion/sync", headers=AUTH).status_code == 400
    assert notion.sync_pending() == {"enabled": False, "synced": 0, "trashed": 0, "failed": 0, "remaining": 0}


def test_sync_lifecycle(client, fake_notion):
    from app import db

    note, article = _setup(client)
    res = notion.sync_pending()
    assert res == {"enabled": True, "synced": 2, "trashed": 0, "failed": 0, "remaining": 0}

    # la base est créée une fois, sous la page parente, avec ses colonnes
    created = fake_notion.made("POST", "/databases")
    assert len(created) == 1
    assert created[0]["parent"] == {"type": "page_id", "page_id": "01234567-89ab-cdef-0123-456789abcdef"}
    assert {"Nom", "Espace", "Catégorie", "Type", "Tags", "Source", "Résumé", "Fiche KB"} <= set(
        created[0]["initial_data_source"]["properties"])

    rows = {r["id"]: r for r in db.fetchall("select id::text, notion_page_id, notion_synced_at from items")}
    assert all(r["notion_page_id"] and r["notion_synced_at"] for r in rows.values())
    page = fake_notion.pages[rows[note]["notion_page_id"]]
    props = page["properties"]
    assert page["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
    assert props["Nom"]["title"][0]["text"]["content"] == "Honnêteté"
    assert props["Espace"]["select"]["name"] == "Perso" and props["Catégorie"]["select"]["name"] == "Valeur"
    assert props["Tags"]["multi_select"][0]["name"] == "éthique"
    assert props["Fiche KB"]["url"] == f"https://kb.example.com/item/{note}"
    assert page["markdown"].startswith("Je dis la vérité, même quand elle coûte.")    # la note d'abord, en entier
    art = fake_notion.pages[rows[article]["notion_page_id"]]
    assert art["properties"]["Source"]["url"] == "https://blog.ex.com/stoicisme"
    assert art["properties"]["Espace"]["select"]["name"] == "Veille"
    assert "## Résumé" in art["markdown"] and "## Contenu" in art["markdown"] and "à relire" in art["markdown"]

    # rien à faire tant que rien ne change (une simple consultation ne compte pas)
    n_calls = len(fake_notion.calls)
    client.get(f"/api/items/{note}", headers=AUTH)
    assert notion.sync_pending()["synced"] == 0 and len(fake_notion.calls) == n_calls

    # modification : page mise à jour en place (même page, propriétés + contenu), avec un 429 au passage
    client.patch(f"/api/items/{note}", json={"title": "Honnêteté radicale"}, headers=AUTH)
    fake_notion.rate_limit_next = True
    assert notion.sync_pending()["synced"] == 1
    page_id = rows[note]["notion_page_id"]
    assert fake_notion.pages[page_id]["properties"]["Nom"]["title"][0]["text"]["content"] == "Honnêteté radicale"
    assert fake_notion.made("PATCH", f"/pages/{page_id}/markdown")
    assert len(fake_notion.made("POST", "/pages")) == 2

    # suppression dans l'app : la page part à la corbeille Notion
    client.delete(f"/api/items/{article}", headers=AUTH)
    assert db.fetchone("select count(*) n from notion_trash")["n"] == 1
    assert notion.sync_pending()["trashed"] == 1
    assert fake_notion.pages[rows[article]["notion_page_id"]]["in_trash"] is True
    assert db.fetchone("select count(*) n from notion_trash")["n"] == 0

    # page supprimée définitivement dans Notion : recréée à la modification suivante
    fake_notion.deleted.add(page_id)
    client.patch(f"/api/items/{note}", json={"user_note": "ma valeur numéro un"}, headers=AUTH)
    notion.sync_pending()
    new_id = db.fetchone("select notion_page_id from items where id = %s", (note,))["notion_page_id"]
    assert new_id != page_id and "ma valeur numéro un" in fake_notion.pages[new_id]["markdown"]

    # statut affiché dans les Réglages
    st = client.get("/api/notion", headers=AUTH).json()
    assert st["configured"] and st["synced"] == 1 and st["pending"] == 0
    assert st["url"] == "https://www.notion.so/db1" and st["last_sync_at"] and st["last_error"] is None


def test_sync_fallbacks(client, fake_notion, monkeypatch):
    from app import db

    note, article = _setup(client)

    # contenu refusé (trop long) : nouvel essai avec une version courte
    monkeypatch.setattr(notion, "SHORT_BODY", 200)
    fake_notion.max_markdown = 1200
    assert notion.sync_pending()["synced"] == 2
    art_page = db.fetchone("select notion_page_id from items where id = %s", (article,))["notion_page_id"]
    assert "contenu tronqué" in fake_notion.pages[art_page]["markdown"]
    fake_notion.max_markdown = None

    # base supprimée dans Notion : recréée, et tout est recopié
    fake_notion.source_gone = True
    db.execute("update items set notion_synced_at = null where id = %s", (note,))
    notion.sync_pending()
    assert notion.get_state()["data_source_id"] is None
    res = notion.sync_pending()
    assert res["synced"] == 2 and fake_notion.databases == 2
    assert {p["parent"]["data_source_id"] for p in fake_notion.pages.values()} >= {"ds2"}

    # modification pendant la copie : l'élément reste à recopier
    real_upsert = notion.upsert_page

    def upsert_then_edit(state, it, lang="fr"):
        page = real_upsert(state, it, lang)
        db.execute("update items set title = 'Édité pendant la copie' where id = %s", (it["id"],))
        return page

    monkeypatch.setattr(notion, "upsert_page", upsert_then_edit)
    db.execute("update items set notion_synced_at = null where id = %s", (note,))
    notion.sync_pending()
    assert db.fetchone("select notion_synced_at from items where id = %s", (note,))["notion_synced_at"] is None
    monkeypatch.setattr(notion, "upsert_page", real_upsert)

    # copie limitée à l'espace Perso : la page de l'article part à la corbeille
    monkeypatch.setattr(get_settings(), "notion_spaces", "perso")
    db.execute("update items set notion_synced_at = null")
    notion.sync_pending()
    row = db.fetchone("select notion_page_id, notion_synced_at from items where id = %s", (article,))
    assert row["notion_page_id"] is None and row["notion_synced_at"] is not None
    assert notion.status()["pending"] == 0


def test_sync_errors_are_reported(client, fake_notion, monkeypatch):
    _setup(client)
    fake_notion.max_markdown = 1                       # tout contenu est refusé…

    def reject_all(request):                           # …et même une page sans contenu
        if request.method == "POST" and request.url.path == "/v1/pages":
            fake_notion.calls.append(("POST", "/pages", {}))
            return httpx.Response(400, json={"code": "validation_error", "message": "Invalid select option"})
        return fake_notion(request)

    monkeypatch.setattr(notion, "_client", httpx.Client(base_url=notion.API, transport=httpx.MockTransport(reject_all)))
    res = notion.sync_pending()
    assert res["failed"] == 2 and res["remaining"] == 2
    assert "Invalid select option" in notion.status()["last_error"]

    called = []
    monkeypatch.setattr(notion, "request_sync", lambda: called.append(True))
    assert client.post("/api/notion/sync", headers=AUTH).json()["ok"] and called


def test_background_syncer(client, fake_notion, monkeypatch):
    import time

    from app import db

    monkeypatch.setattr(notion, "_syncer", None)
    _setup(client)
    syncer = notion.Syncer()
    syncer.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if db.fetchone("select count(*) filter (where notion_synced_at is not null) n from items")["n"] == 2:
                break
            time.sleep(0.05)
        else:
            pytest.fail("la synchro de fond n'a rien copié")
        assert syncer.alive
    finally:
        syncer.stop()


def test_delete_during_sync_and_stuck_trash(client, fake_notion, monkeypatch):
    from app import db

    note, article = _setup(client)

    # l'élément est supprimé dans l'app pendant que sa page est créée : la page part quand même à la corbeille
    real_upsert = notion.upsert_page

    def upsert_then_delete(state, it, lang="fr"):
        page = real_upsert(state, it, lang)
        if it["id"] == article:
            client.delete(f"/api/items/{article}", headers=AUTH)
        return page

    monkeypatch.setattr(notion, "upsert_page", upsert_then_delete)
    notion.sync_pending()
    monkeypatch.setattr(notion, "upsert_page", real_upsert)
    orphan = next(pid for pid, body in fake_notion.pages.items()
                  if body["properties"]["ID KB"]["rich_text"][0]["text"]["content"] == article)
    assert db.fetchone("select count(*) n from notion_trash")["n"] == 1
    notion.sync_pending()
    assert fake_notion.pages[orphan].get("in_trash") is True

    # une page impossible à mettre à la corbeille ne bloque pas la copie des autres éléments
    db.execute("insert into notion_trash (page_id) values ('page-verrouillee')")

    def forbid_trash(request):
        if request.method == "PATCH" and request.url.path == "/v1/pages/page-verrouillee":
            return httpx.Response(403, json={"code": "restricted_resource", "message": "No access"})
        return fake_notion(request)

    monkeypatch.setattr(notion, "_client", httpx.Client(base_url=notion.API, transport=httpx.MockTransport(forbid_trash)))
    client.patch(f"/api/items/{note}", json={"title": "Nouveau titre"}, headers=AUTH)
    res = notion.sync_pending()
    assert res["synced"] == 1 and res["trashed"] == 0
    assert "page-verrouillee" in notion.status()["last_error"]
    assert db.fetchone("select count(*) n from notion_trash")["n"] == 1          # réessayée plus tard


def test_copy_language(client, fake_notion):
    """English copy: a new database with English columns, cards and headings; the French one is left as it was."""
    note, article = _setup(client)
    notion.sync_pending()
    assert fake_notion.databases == 1
    fr_pages = dict(fake_notion.pages)
    first = fake_notion.made("POST", "/databases")[0]["initial_data_source"]["properties"]
    assert "Résumé" in first and "Summary" not in first
    st = client.get("/api/notion", headers=AUTH).json()
    assert st["language"] == "fr" and st["languages"] == ["fr", "en"]

    assert client.put("/api/notion/language", json={"language": "de"}, headers=AUTH).status_code == 400
    st = client.put("/api/notion/language", json={"language": "en"}, headers=AUTH).json()
    assert st["language"] == "en" and st["pending"] == 2          # everything to copy again

    notion.sync_pending()
    assert fake_notion.databases == 2
    schema = fake_notion.made("POST", "/databases")[1]["initial_data_source"]["properties"]
    assert {"Name", "Space", "Category", "Summary", "KB card"} <= set(schema)
    assert {o["name"] for o in schema["Category"]["select"]["options"]} >= {"Value", "Principle"}
    new_pages = {k: v for k, v in fake_notion.pages.items() if k not in fr_pages}
    assert len(new_pages) == 2 and all(p["parent"]["data_source_id"] == "ds2" for p in new_pages.values())
    by_id = {p["properties"]["KB ID"]["rich_text"][0]["text"]["content"]: p for p in new_pages.values()}
    art = by_id[article]
    assert art["properties"]["Space"]["select"]["name"] == "Feed"
    assert art["properties"]["Summary"]["rich_text"][0]["text"]["content"].startswith("Summary of the article")
    assert "## Summary" in art["markdown"] and "## Key points" in art["markdown"] and "Why I kept it:" in art["markdown"]
    assert "Point A (en)" in art["markdown"]
    val = by_id[note]
    assert val["properties"]["Category"]["select"]["name"] == "Value"
    assert val["properties"]["Name"]["title"][0]["text"]["content"] == "Honnêteté"    # a title he wrote stays his
    assert val["markdown"].startswith("Je dis la vérité, même quand elle coûte.")    # and so does the note
    assert client.get("/api/notion", headers=AUTH).json()["pending"] == 0


def test_export_language(client):
    import io
    import zipfile

    _setup(client)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export?lang=en", headers=AUTH).content))
    names = z.namelist()
    assert any(n.startswith("KB/Feed/") for n in names) and any(n.startswith("KB/Personal/Values/") for n in names)
    article = z.read(next(n for n in names if n.startswith("KB/Feed/"))).decode()
    assert "## Summary\nSummary of the article" in article and "> **Why I kept it:** à relire" in article
    fr = zipfile.ZipFile(io.BytesIO(client.get("/api/export", headers=AUTH).content))
    assert any(n.startswith("KB/Perso/Valeurs/") for n in fr.namelist())


def test_page_not_given_to_the_connection(client, fake_notion, monkeypatch):
    """A new Notion connection sees no page: the error says how to give it the parent page."""
    _setup(client)

    def no_access(request):
        if request.method == "POST" and request.url.path == "/v1/databases":
            return httpx.Response(404, json={"code": "object_not_found", "message": "Could not find page"})
        return fake_notion(request)

    monkeypatch.setattr(notion, "_client", httpx.Client(base_url=notion.API, transport=httpx.MockTransport(no_access)))
    with pytest.raises(notion.NotionError) as err:
        notion.sync_pending()
    notion._record_error(err.value)                    # what the background sync does with it
    error = notion.status()["last_error"]
    assert "page parente introuvable" in error and "Ajouter une connexion" in error
