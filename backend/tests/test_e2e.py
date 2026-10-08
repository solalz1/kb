"""Bout en bout : ingestion → worker → base → recherche → API → chat → MCP (Claude simulé, vraie base Postgres)."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import extractors
from app.extractors.base import Extracted

from .conftest import drain

AUTH = {"Authorization": "Bearer test-token"}

FAKE_PAGES = {
    "https://blog.ex.com/rag-eval": Extracted(
        kind="article", title="Évaluer un système RAG", source_url="https://blog.ex.com/rag-eval",
        author="Jane Doe", site_name="Le Blog IA",
        content="L'évaluation des systèmes RAG repose sur la précision du retrieval et la fidélité des réponses. "
                "Utiliser des jeux de questions réalistes et mesurer régulièrement la qualité. " * 6,
    ),
    "https://x.com/alice/status/555": Extracted(
        kind="tweet", source_url="https://x.com/alice/status/555", author="Alice (@alice)", site_name="X",
        content="@alice (Alice)\n\nLes agents RAG en production : retrieval hybride, reranking et évaluation continue.",
        metadata={"tweet_id": "555"},
    ),
    "https://cuisine.ex.com/tarte": Extracted(
        kind="article", title="Recette de tarte aux pommes", source_url="https://cuisine.ex.com/tarte",
        content="Préchauffer le four, étaler la pâte, disposer les pommes en rosace, cuire quarante minutes. " * 5,
    ),
}


@pytest.fixture
def client(clean_db, fake_llm, monkeypatch):
    def fake_extract_url(url):
        info = __import__("app.urls", fromlist=["classify"]).classify(url)
        return FAKE_PAGES[info.canonical]

    monkeypatch.setattr(extractors, "extract_url", fake_extract_url)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _ingest_all(client):
    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/rag-eval?utm_source=x", "note": "pour mon projet RAG"}, headers=AUTH)
    assert r.status_code == 200, r.text
    r = client.post("/api/ingest", json={"url": "https://twitter.com/alice/status/555?s=20"}, headers=AUTH)
    assert r.json()["message"] == "Ajouté à ta KB ✓"
    client.post("/api/ingest", json={"text": "https://cuisine.ex.com/tarte"}, headers=AUTH)
    client.post("/api/ingest", json={"text": "Idée : construire un agent qui résume mes tweets sauvegardés chaque semaine."}, headers=AUTH)
    from PIL import Image

    img = io.BytesIO()
    Image.new("RGB", (30, 30), "blue").save(img, format="PNG")
    r = client.post("/api/ingest", files={"file": ("capture.png", img.getvalue(), "image/png")},
                    data={"note": "capture vue sur X"}, headers=AUTH)
    assert r.status_code == 200, r.text
    return drain()


def test_full_flow(client, fake_llm):
    # --- sécurité ---
    assert client.post("/api/ingest", json={"url": "https://a.b"}).status_code == 401
    assert client.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401

    # --- ingestion + traitement ---
    assert _ingest_all(client) == 5
    r = client.post("/api/ingest", json={"url": "https://x.com/i/status/555"}, headers=AUTH)
    assert r.json()["items"][0]["duplicate"] is True          # même tweet, autre forme d'URL

    items = client.get("/api/items", headers=AUTH).json()
    assert items["total"] == 5
    assert all(i["status"] == "ready" for i in items["items"]), items
    kinds = sorted(i["kind"] for i in items["items"])
    assert kinds == ["article", "article", "image", "note", "tweet"]

    rag = next(i for i in items["items"] if i["title"] == "Évaluer un système RAG")
    assert rag["source_url"] == "https://blog.ex.com/rag-eval"
    assert rag["user_note"] == "pour mon projet RAG"
    image = next(i for i in items["items"] if i["kind"] == "image")
    assert image["thumbnail"] and "/api/local-files/" in image["thumbnail"]
    assert client.get(image["thumbnail"]).status_code == 200

    # --- fiche ---
    detail = client.get(f"/api/items/{rag['id']}", headers=AUTH).json()
    assert detail["summary"].startswith("Résumé de article")
    assert detail["actions"][0]["text"] == "Tester l'outil mentionné"
    assert detail["view_count"] == 1
    assert any(l["id"] for l in detail["links"])                # liens automatiques calculés

    # --- recherche hybride ---
    found = client.get("/api/items", params={"q": "fidélité des réponses retrieval"}, headers=AUTH).json()
    assert found["search"] and found["items"][0]["id"] == rag["id"]
    found = client.get("/api/items", params={"q": "pommes four pâte"}, headers=AUTH).json()
    assert found["items"][0]["title"] == "Recette de tarte aux pommes"
    only_tweets = client.get("/api/items", params={"q": "agents", "kind": "tweet"}, headers=AUTH).json()
    assert {i["kind"] for i in only_tweets["items"]} == {"tweet"}

    # --- édition, tags, entités, actions, stats ---
    assert client.patch(f"/api/items/{rag['id']}", json={"tags": ["RAG", "évaluation"], "pinned": True}, headers=AUTH).json()["ok"]
    assert client.get("/api/items", params={"tag": "rag"}, headers=AUTH).json()["total"] == 1
    assert client.get("/api/items", headers=AUTH).json()["items"][0]["id"] == rag["id"]   # épinglé en tête
    ents = client.get("/api/entities", headers=AUTH).json()
    assert ents[0]["name"] == "Andrej Karpathy" and ents[0]["count"] == 5
    assert client.get("/api/items", params={"entity": "Andrej Karpathy"}, headers=AUTH).json()["total"] == 5
    acts = client.get("/api/actions", headers=AUTH).json()
    assert len(acts) == 5
    client.patch(f"/api/actions/{acts[0]['id']}", json={"done": True}, headers=AUTH)
    assert len(client.get("/api/actions", headers=AUTH).json()) == 4
    stats = client.get("/api/stats", headers=AUTH).json()
    assert stats["total"] == 5 and stats["open_actions"] == 4

    # --- chat (SSE) ---
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "Comment évaluer un RAG ?"}]}, headers=AUTH)
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "status" and "sources" in types and types[-1] == "done"
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources[0]["source_url"] and sources[0]["kb_url"].startswith("https://kb.example.com/item/")
    assert "".join(e.get("text", "") for e in events if e["type"] == "delta") == "Réponse sourcée [1]."
    prompt = fake_llm["stream"][-1]["messages"][-1]["content"]
    assert "<sources>" in prompt and "URL : https://" in prompt

    # suite de conversation : reformulation + historique nettoyé des [n]
    r = client.post("/api/chat", json={"messages": [
        {"role": "user", "content": "Comment évaluer un RAG ?"},
        {"role": "assistant", "content": "En mesurant la fidélité [1]."},
        {"role": "user", "content": "Et pour le retrieval ?"}]}, headers=AUTH)
    sent = fake_llm["stream"][-1]["messages"]
    assert sent[1]["content"] == "En mesurant la fidélité."
    assert "(reformulée)" in next(json.loads(l[6:]) for l in r.text.splitlines() if '"sources"' in l)["query"]

    # choix du modèle
    options = client.get("/api/models", headers=AUTH).json()
    assert [o["id"] for o in options][:2] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert next(o for o in options if o["default"])["id"] == "claude-sonnet-5-5"
    assert fake_llm["stream"][-1]["model"] == "claude-sonnet-5-5"          # défaut quand rien n'est choisi
    client.post("/api/chat", json={"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "Et Opus ?"}]}, headers=AUTH)
    assert fake_llm["stream"][-1]["model"] == "claude-opus-5-5"
    r = client.post("/api/chat", json={"model": "gpt-9", "messages": [{"role": "user", "content": "?"}]}, headers=AUTH)
    assert r.status_code == 400

    # mode projet
    r = client.post("/api/chat", json={"mode": "project", "messages": [{"role": "user", "content": "Un SaaS d'évaluation RAG"}]}, headers=AUTH)
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    plan = next(e for e in events if e["type"] == "plan")
    assert plan["queries"] == ["agents rag", "évaluation"]
    assert "## Angles morts" in fake_llm["stream"][-1]["system"]

    # --- export Obsidian ---
    r = client.get("/api/export", headers=AUTH)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert len(names) == 5
    md = z.read("KB/Veille/Évaluer un système RAG.md").decode()
    assert md.startswith("---\ntitle:") and "source: \"https://blog.ex.com/rag-eval\"" in md and "## Liés" in md

    # --- retraitement et suppression ---
    note = next(i for i in items["items"] if i["kind"] == "note")
    assert client.post(f"/api/items/{note['id']}/reprocess", headers=AUTH).json()["ok"]
    assert drain() == 1
    assert client.delete(f"/api/items/{image['id']}", headers=AUTH).json()["ok"]
    assert client.get("/api/items", headers=AUTH).json()["total"] == 4


def test_failures_and_retries(client, monkeypatch):
    from app import db
    from app.extractors import ExtractionError

    def boom(url):
        raise ExtractionError("Tweet introuvable")

    monkeypatch.setattr(extractors, "extract_url", boom)
    item_id = client.post("/api/ingest", json={"url": "https://x.com/a/status/1"}, headers=AUTH).json()["id"]
    drain()
    row = db.fetchone("select status, error from items where id = %s", (item_id,))
    assert row["status"] == "error" and "introuvable" in row["error"]

    def flaky(url):
        raise RuntimeError("réseau")

    monkeypatch.setattr(extractors, "extract_url", flaky)
    item_id = client.post("/api/ingest", json={"url": "https://x.com/a/status/2"}, headers=AUTH).json()["id"]
    drain()
    row = db.fetchone("select status, attempts, next_attempt_at from items where id = %s", (item_id,))
    assert row["status"] == "pending" and row["attempts"] == 1 and row["next_attempt_at"] is not None


def _mcp(client, path, method, params=None, headers=None, rid=1):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
         "MCP-Protocol-Version": "2025-06-18", **(headers or {})}
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return client.post(path, json=body, headers=h)


def test_mcp_server(client):
    _ingest_all(client)
    init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}

    # accès refusé sans secret
    assert _mcp(client, "/mcp", "initialize", init).status_code == 401
    assert _mcp(client, "/mcp/mauvais-secret", "initialize", init).status_code in (401, 404)

    r = _mcp(client, "/mcp/mcp-secret", "initialize", init)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "Knowledge base"

    tools = _mcp(client, "/mcp/mcp-secret", "tools/list", rid=2).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"search_kb", "get_item", "find_for_project", "add_to_kb", "list_recent", "get_related",
            "list_actions", "resurface", "browse_kb", "kb_overview"} <= names

    # Claude Code : même serveur via en-tête Authorization
    r = _mcp(client, "/mcp", "tools/call", {"name": "search_kb", "arguments": {"query": "fidélité retrieval RAG"}},
             headers={"Authorization": "Bearer mcp-secret"}, rid=3)
    text = r.json()["result"]["content"][0]["text"]
    assert "### Évaluer un système RAG" in text
    assert "Source originale : https://blog.ex.com/rag-eval" in text
    assert "Fiche KB : https://kb.example.com/item/" in text

    item_id = text.split("(id : ")[1].split(")")[0]
    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "get_item", "arguments": {"item_id": item_id, "include_full_content": True}}, rid=4)
    full = r.json()["result"]["content"][0]["text"]
    assert "Contenu complet" in full and "Points clés" in full

    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "find_for_project", "arguments": {"project_description": "un outil d'évaluation RAG"}}, rid=5)
    assert "Projet compris : Projet test" in r.json()["result"]["content"][0]["text"]

    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "add_to_kb", "arguments": {"text": "Note ajoutée depuis Claude"}}, rid=6)
    assert r.json()["result"]["content"][0]["text"].startswith("Ajouté")

    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "browse_kb", "arguments": {"kind": "article", "limit": 1}}, rid=8)
    text = r.json()["result"]["content"][0]["text"]
    assert text.startswith("2 élément(s) au total") and "offset=1" in text
    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "browse_kb", "arguments": {"entity": "andrej karpathy"}}, rid=9)
    assert r.json()["result"]["content"][0]["text"].startswith("5 élément(s)")
    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "kb_overview", "arguments": {}}, rid=10)
    overview = r.json()["result"]["content"][0]["text"]
    assert overview.startswith("5 éléments") and "Andrej Karpathy [person] (5)" in overview

    for name, args in [("list_recent", {}), ("list_actions", {}), ("get_related", {"item_id": item_id}), ("resurface", {})]:
        r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": args}, rid=7)
        assert r.status_code == 200 and not r.json()["result"].get("isError"), (name, r.text)


def test_ingest_rules_and_routing(client):
    from app import db

    long_page_text = "texte de la page " * 300
    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/rag-eval", "text": long_page_text}, headers=AUTH)
    row = db.fetchone("select input_text from items where id = %s", (r.json()["id"],))
    assert row["input_text"] is None                       # texte de page Safari ignoré
    r = client.post("/api/ingest", json={"text": "Regarde ça ! https://x.com/alice/status/555"}, headers=AUTH)
    row = db.fetchone("select input_url, input_text, kind from items where id = %s", (r.json()["id"],))
    assert row["input_url"] == "https://x.com/alice/status/555" and row["kind"] == "tweet"
    r = client.post("/api/ingest", content="une note en texte brut", headers={**AUTH, "Content-Type": "text/plain"})
    assert db.fetchone("select kind from items where id = %s", (r.json()["id"],))["kind"] == "note"
    assert client.post("/api/ingest", json={}, headers=AUTH).status_code == 400

    # routes protégées / introuvables
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404
    assert client.get("/api/nope", headers=AUTH).status_code == 404
    assert client.post("/mcp/autre/chemin", json={}).status_code == 401
    assert client.get("/").status_code == 200              # front non buildé : message JSON


def test_queue_recovers_stale_items(clean_db):
    from app import db

    stuck = db.fetchone("""insert into items (input_text, kind, status, attempts, locked_at)
                           values ('a', 'note', 'processing', 3, now() - interval '2 hours') returning id""")["id"]
    retry = db.fetchone("""insert into items (input_text, kind, status, attempts, locked_at)
                           values ('b', 'note', 'processing', 1, now() - interval '2 hours') returning id""")["id"]
    claimed = db.fetchone("select * from claim_next_item()")
    assert claimed["id"] == retry and claimed["attempts"] == 2      # repris après un redémarrage
    row = db.fetchone("select status, error from items where id = %s", (stuck,))
    assert row["status"] == "error" and "interrompu" in row["error"]  # pas de boucle infinie
