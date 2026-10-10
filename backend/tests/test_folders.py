"""Folders: default shelves, automatic filing by Claude, filing by hand (app and Shortcut), and browsing by folder."""

import pytest
from fastapi.testclient import TestClient

from app import extractors
from app.extractors.base import Extracted

from .conftest import drain
from .test_e2e import AUTH, _mcp

DEFAULTS = ["ML", "Claude", "Entretien", "Perso"]
PAGES = {
    "https://blog.example.com/agents": Extracted(
        kind="article", title="Des agents avec Claude", source_url="https://blog.example.com/agents",
        author="Jane Doe", content="Construire des agents avec Claude Code et le protocole MCP. " * 10),
    "https://blog.example.com/transformers": Extracted(
        kind="article", title="Les transformers expliqués", source_url="https://blog.example.com/transformers",
        author="Jane Doe", content="Un tour du ML moderne : attention, transformers, entraînement. " * 10),
    "https://blog.example.com/cuisine": Extracted(
        kind="article", title="Le pain au levain", source_url="https://blog.example.com/cuisine",
        author="Jane Doe", content="Farine, eau, sel et patience : la recette du pain au levain. " * 10),
}


@pytest.fixture
def client(clean_db, fake_llm, monkeypatch):
    monkeypatch.setattr(extractors, "extract_url", lambda url: PAGES[url])
    from app.main import app

    with TestClient(app) as c:       # the start creates the default folders
        yield c


def _folders(client):
    r = client.get("/api/folders", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(client):
    return {f["name"]: f for f in _folders(client)["folders"]}


def _item(client, item_id):
    return client.get(f"/api/items/{item_id}", headers=AUTH).json()


def _share(client, **body):
    r = client.post("/api/ingest", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_default_folders_are_created_once(client):
    from app import folders

    assert [f["name"] for f in _folders(client)["folders"]] == DEFAULTS
    assert all(f["description"] and f["count"] == 0 for f in _folders(client)["folders"])
    # deleted by the user: it doesn't come back at the next start
    assert client.delete(f"/api/folders/{_by_name(client)['Entretien']['id']}", headers=AUTH).status_code == 200
    folders.seed()
    assert [f["name"] for f in _folders(client)["folders"]] == ["ML", "Claude", "Perso"]


def test_create_rename_and_delete(client):
    r = client.post("/api/folders", json={"name": "  Lecture   du soir ", "description": "Romans et essais"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    reading = r.json()
    assert reading["name"] == "Lecture du soir" and reading["position"] == len(DEFAULTS)
    for bad, why in [("", "nom"), ("ml", "existe déjà"), ("Entretién", "existe déjà"), ("Automatique", "réservé"),
                     ("Espace perso", "réservé"), ("x" * 61, "trop long")]:
        r = client.post("/api/folders", json={"name": bad}, headers=AUTH)
        assert r.status_code == 400 and why in r.json()["detail"], (bad, r.text)

    r = client.patch(f"/api/folders/{reading['id']}", json={"name": "Lectures", "description": ""}, headers=AUTH)
    assert r.json()["name"] == "Lectures" and r.json()["description"] is None
    assert client.patch(f"/api/folders/{reading['id']}", json={"name": "Claude"}, headers=AUTH).status_code == 400
    # its own name in another case is fine
    assert client.patch(f"/api/folders/{reading['id']}", json={"name": "LECTURES"}, headers=AUTH).status_code == 200
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"/api/folders/{unknown}", json={"name": "X"}, headers=AUTH).status_code == 404
    assert client.patch("/api/folders/pas-un-id", json={"name": "X"}, headers=AUTH).status_code == 404
    assert client.delete(f"/api/folders/{unknown}", headers=AUTH).status_code == 404
    assert client.get("/api/folders", headers={}).status_code == 401


def test_claude_files_new_items(client, fake_llm):
    agents = _share(client, url="https://blog.example.com/agents")
    ml = _share(client, url="https://blog.example.com/transformers")
    bread = _share(client, url="https://blog.example.com/cuisine")
    drain()
    shelves = _by_name(client)
    assert _item(client, agents)["folder_id"] == shelves["Claude"]["id"]
    assert _item(client, ml)["folder_id"] == shelves["ML"]["id"]
    assert _item(client, bread)["folder_id"] is None
    assert [f["name"] for f in fake_llm["enrich"][0]["folders"]] == DEFAULTS
    assert shelves["Claude"]["count"] == 1 and _folders(client)["unfiled"] == 1

    # browsing a folder, and what's in none
    r = client.get("/api/items", params={"folder": shelves["ML"]["id"]}, headers=AUTH).json()
    assert [i["id"] for i in r["items"]] == [ml] and r["total"] == 1 and r["items"][0]["folder_id"] == shelves["ML"]["id"]
    r = client.get("/api/items", params={"folder": "none"}, headers=AUTH).json()
    assert [i["id"] for i in r["items"]] == [bread]
    r = client.get("/api/items", params={"folder": shelves["ML"]["id"], "q": "transformers attention"}, headers=AUTH)
    assert [i["id"] for i in r.json()["items"]] == [ml]
    assert client.get("/api/items", params={"folder": "00000000-0000-0000-0000-000000000000"},
                      headers=AUTH).status_code == 404


def test_a_folder_chosen_by_hand_sticks(client, fake_llm):
    shelves = _by_name(client)
    # chosen in the Shortcut, by name in any case: Claude isn't asked, even when reprocessing
    agents = _share(client, url="https://blog.example.com/agents", folder="entretien")
    drain()
    assert _item(client, agents)["folder_id"] == shelves["Entretien"]["id"]
    assert fake_llm["enrich"][-1]["folders"] is None
    assert client.post(f"/api/items/{agents}/reprocess", headers=AUTH).status_code == 200
    drain()
    assert _item(client, agents)["folder_id"] == shelves["Entretien"]["id"]

    # moved in the app, then out of any folder
    ml = _share(client, url="https://blog.example.com/transformers")
    drain()
    assert client.patch(f"/api/items/{ml}", json={"folder_id": shelves["Perso"]["id"]}, headers=AUTH).status_code == 200
    assert _item(client, ml)["folder_id"] == shelves["Perso"]["id"]
    assert client.patch(f"/api/items/{ml}", json={"folder_id": None}, headers=AUTH).status_code == 200
    assert _item(client, ml)["folder_id"] is None
    assert client.patch(f"/api/items/{ml}", json={"folder_id": "00000000-0000-0000-0000-000000000000"},
                        headers=AUTH).status_code == 404
    # "Ranger automatiquement" leaves both alone
    r = client.post("/api/folders/sort", headers=AUTH)
    assert r.status_code == 200, r.text
    assert _item(client, ml)["folder_id"] is None and _item(client, agents)["folder_id"] == shelves["Entretien"]["id"]

    # shared again into another folder: it moves
    assert _share(client, url="https://blog.example.com/agents", folder="Claude") == agents
    assert _item(client, agents)["folder_id"] == shelves["Claude"]["id"]

    # deleting the folder unfiles its items, and they can be sorted again
    client.delete(f"/api/folders/{shelves['Claude']['id']}", headers=AUTH)
    assert _item(client, agents)["folder_id"] is None
    client.post("/api/folders", json={"name": "Claude"}, headers=AUTH)
    client.post("/api/folders/sort", headers=AUTH)
    assert _item(client, agents)["folder_id"] == _by_name(client)["Claude"]["id"]


def test_shortcut_choices_follow_the_folders(client):
    r = client.get("/api/folders/choices", headers=AUTH)
    assert r.json() == {"choices": ["Automatique", *DEFAULTS, "Espace Perso"]}
    client.post("/api/folders", json={"name": "Lecture"}, headers=AUTH)
    client.delete(f"/api/folders/{_by_name(client)['ML']['id']}", headers=AUTH)
    assert client.get("/api/folders/choices", headers=AUTH).json()["choices"] == [
        "Automatique", "Claude", "Entretien", "Perso", "Lecture", "Espace Perso"]
    assert client.get("/api/folders/choices").status_code == 401


def test_what_the_shortcut_sends_back(client, fake_llm):
    # Espace Perso: the Perso space (notes about yourself), filed by Claude
    perso = _share(client, text="Toujours finir ce que je commence", folder="Espace Perso")
    # Automatique, or a folder deleted since the list was fetched: Claude chooses
    auto = _share(client, url="https://blog.example.com/agents", folder="Automatique")
    gone = _share(client, url="https://blog.example.com/transformers", folder="Dossier supprimé")
    # a file, in a form
    r = client.post("/api/ingest", data={"folder": "Perso", "note": "à lire"},
                    files={"file": ("notes.txt", b"Mes notes de lecture sur la discipline.", "text/plain")},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    upload = r.json()["id"]
    drain()
    shelves = _by_name(client)
    assert _item(client, perso)["space"] == "perso"
    assert _item(client, auto)["folder_id"] == shelves["Claude"]["id"]
    assert _item(client, gone)["folder_id"] == shelves["ML"]["id"]
    assert _item(client, upload)["folder_id"] == shelves["Perso"]["id"]
    assert _item(client, upload)["metadata"]["manual_folder"] is True

    # a note written in the app, filed by hand
    r = client.post("/api/notes", json={"content": "Idées pour l'entretien de jeudi", "space": "main",
                                        "folder": shelves["Entretien"]["id"]}, headers=AUTH)
    drain()
    assert _item(client, r.json()["id"])["folder_id"] == shelves["Entretien"]["id"]


def test_items_saved_before_folders_are_sorted_once(client, fake_llm):
    from app import db, folders

    db.execute("delete from folders")
    db.execute("delete from kb_settings where key in ('folders_seeded', 'folders_sorted')")
    agents = _share(client, url="https://blog.example.com/agents")
    ml = _share(client, url="https://blog.example.com/transformers")
    drain()
    assert _item(client, agents)["folder_id"] is None        # no folders yet

    folders.startup()
    shelves = _by_name(client)
    assert _item(client, agents)["folder_id"] == shelves["Claude"]["id"]
    assert _item(client, ml)["folder_id"] == shelves["ML"]["id"]
    calls = len(fake_llm["classify"])
    folders.startup()                                        # once only
    assert len(fake_llm["classify"]) == calls

    # the sort answers with the new counts; Claude unreachable: the app says why
    r = client.post("/api/folders/sort", headers=AUTH).json()
    assert r["sorted"] == 2 and r["filed"] == 2 and _by_name(client)["ML"]["count"] == 1

    def down(items, shelves):
        raise RuntimeError("overloaded")

    from app import llm

    llm.classify_folders, saved = down, llm.classify_folders
    try:
        r = client.post("/api/folders/sort", headers=AUTH)
        assert r.status_code == 502 and "overloaded" in r.json()["detail"]
    finally:
        llm.classify_folders = saved


def test_connector_browses_by_folder(client):
    agents = _share(client, url="https://blog.example.com/agents")
    _share(client, url="https://blog.example.com/transformers")
    drain()

    def call(name, **arguments):
        r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": arguments})
        assert r.status_code == 200, r.text
        return r.json()["result"]["content"][0]["text"]

    assert "Dossiers (browse_kb folder=…) : ML 1, Claude 1, Entretien 0, Perso 0" in call("kb_overview")
    text = call("browse_kb", folder="claude")
    assert agents in text and "transformers" not in text
    assert "Dossier inconnu : Cuisine (dossiers : ML, Claude, Entretien, Perso)" in call("browse_kb", folder="Cuisine")


def test_enrich_asks_for_a_folder(monkeypatch):
    from app import llm

    seen = {}

    def answer(**kw):
        seen.update(kw)
        return {"title": "T", "summary": "S", "key_points": [], "tags": [], "entities": [], "use_cases": [],
                "action_items": [], "genre": "other", "language": "fr", "folder": "ML",
                "translation": {"title": "T", "summary": "S", "key_points": [], "use_cases": []}}

    monkeypatch.setattr(llm, "call_tool", answer)
    shelves = [{"name": "ML", "description": "Machine learning"}, {"name": "Claude", "description": None}]
    out = llm.enrich(kind="article", title=None, author=None, source_url=None, published_at=None, content="x",
                     user_note=None, existing_tags=[], folders=shelves)
    assert out["folder"] == "ML"
    assert seen["schema"]["properties"]["folder"]["enum"] == ["ML", "Claude", "aucun"]
    assert "folder" in seen["schema"]["required"]
    assert "- ML : Machine learning\n  - Claude" in seen["system"]

    # no folders: not asked; a name that isn't a folder ("aucun", or made up) files nowhere
    out = llm.enrich(kind="article", title=None, author=None, source_url=None, published_at=None, content="x",
                     user_note=None, existing_tags=[])
    assert "folder" not in seen["schema"]["properties"] and out["folder"] is None
    answer_folder = {"aucun": None, "Cuisine": None, "Claude": "Claude"}
    for sent, kept in answer_folder.items():
        monkeypatch.setattr(llm, "call_tool", lambda **kw: {**answer(**kw), "folder": sent})
        assert llm.enrich(kind="article", title=None, author=None, source_url=None, published_at=None, content="x",
                          user_note=None, existing_tags=[], folders=shelves)["folder"] == kept


def test_classify_folders_keeps_only_real_answers(monkeypatch):
    from app import llm

    seen = {}

    def answer(**kw):
        seen.update(kw)
        return {"items": [{"id": "a", "folder": "ML"}, {"id": "b", "folder": "aucun"}, {"id": "c", "folder": "Cuisine"},
                          {"id": "z", "folder": "ML"}, "pas un objet"]}

    monkeypatch.setattr(llm, "call_tool", answer)
    items = [{"id": "a", "kind": "tweet", "title": "Scaling laws", "summary": "Plus de calcul.", "tags": ["llm"]},
             {"id": "b", "kind": "note", "title": None, "summary": None, "tags": []},
             {"id": "c", "kind": "article", "title": "Pain", "summary": "Levain", "tags": None}]
    out = llm.classify_folders(items, [{"name": "ML", "description": "IA"}])
    assert out == {"a": "ML"}
    assert "id=a | tweet | Scaling laws | Plus de calcul. | tags : llm" in seen["content"]
    assert "id=b | note | (sans titre) | " in seen["content"]
    assert seen["schema"]["properties"]["items"]["items"]["properties"]["folder"]["enum"] == ["ML", "aucun"]
    monkeypatch.setattr(llm, "call_tool", lambda **kw: {"items": "<item>cassé</item>"})
    assert llm.classify_folders(items, [{"name": "ML", "description": "IA"}]) == {}


def test_export_names_the_folder(client):
    import io
    import zipfile

    agents = _share(client, url="https://blog.example.com/agents")
    _share(client, url="https://blog.example.com/cuisine")
    drain()
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export", headers=AUTH).content))
    pages = {n: z.read(n).decode() for n in z.namelist() if n.endswith(".md")}
    agents_page = next(t for t in pages.values() if agents in t)
    bread_page = next(t for t in pages.values() if "levain" in t.lower() and agents not in t)
    assert 'folder: "Claude"' in agents_page and "folder:" not in bread_page


def test_list_filters_several_kinds_and_gives_durations(client):
    from app import db

    for kind, title, meta in [("youtube", "Une vidéo YouTube", {"duration": 7440}), ("video", "Une vidéo envoyée", {}),
                              ("tweet", "Un tweet", {})]:
        db.execute("insert into items (kind, title, status, metadata) values (%s, %s, 'ready', %s)",
                   (kind, title, db.jsonb(meta)))
    r = client.get("/api/items", params={"kind": "youtube,video"}, headers=AUTH).json()
    assert sorted(i["title"] for i in r["items"]) == ["Une vidéo YouTube", "Une vidéo envoyée"] and r["total"] == 2
    assert {i["title"]: i["duration"] for i in r["items"]} == {"Une vidéo YouTube": 7440, "Une vidéo envoyée": None}
    assert client.get("/api/items", params={"kind": "tweet"}, headers=AUTH).json()["total"] == 1
