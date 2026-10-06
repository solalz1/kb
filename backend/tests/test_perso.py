"""Espace Perso : notes écrites à la main, rangement, édition, mode Conseil, connecteur et export."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import extractors
from app.extractors.base import Extracted

from .conftest import drain
from .test_e2e import AUTH, _mcp

ARTICLE = Extracted(
    kind="article", title="Dire non sans culpabiliser", source_url="https://blog.ex.com/dire-non",
    author="Jane Doe", content="Savoir dire non protège ton temps et tes priorités. " * 20,
)


@pytest.fixture
def client(clean_db, fake_llm, monkeypatch):
    monkeypatch.setattr(extractors, "extract_url", lambda url: ARTICLE)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _events(r):
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def _note(client, **body):
    r = client.post("/api/notes", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_manual_notes_and_routing(client, fake_llm):
    from app import db

    principle = _note(client, title="Ma règle des 24 heures",
                      content="Avant toute décision importante, j'attends 24 heures et j'en parle à quelqu'un.",
                      category="principe", tags=["Décision"])
    auto = _note(client, content="J'ai accepté trop de projets en même temps et tout a pris du retard.")
    assert client.post("/api/notes", json={"content": "   "}, headers=AUTH).status_code == 400
    assert client.post("/api/notes", json={"content": "x", "category": "inconnue"}, headers=AUTH).status_code == 400

    # rangement par hashtags depuis le Raccourci
    r = client.post("/api/ingest", json={"text": "Toujours finir ce que je commence #perso #Leçons"}, headers=AUTH)
    tagged = r.json()["id"]
    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non", "note": "à relire #perso"}, headers=AUTH)
    shared = r.json()["id"]
    r = client.post("/api/ingest", json={"text": "Le #journal de 20h parlait d'IA ce soir"}, headers=AUTH)
    plain = r.json()["id"]
    # une note perso qui contient un lien reste une note
    r = client.post("/api/ingest", json={"text": "Relire https://ex.com/stoicisme", "space": "perso"}, headers=AUTH)
    with_link = r.json()["id"]
    assert drain() == 6

    rows = {r["id"]: r for r in db.fetchall(
        "select id::text, space, category, title, input_text, user_note, tags, kind, status from items")}
    assert all(r["status"] == "ready" for r in rows.values())
    assert rows[principle]["space"] == "perso" and rows[principle]["category"] == "principe"
    assert rows[principle]["title"] == "Ma règle des 24 heures"            # titre écrit à la main conservé
    assert rows[principle]["tags"][0] == "décision"                        # tags choisis gardés en tête
    assert rows[auto]["space"] == "perso" and rows[auto]["category"] == "lecon"   # catégorie proposée par Claude
    assert (rows[tagged]["space"], rows[tagged]["category"]) == ("perso", "lecon")
    assert rows[tagged]["input_text"] == "Toujours finir ce que je commence"      # hashtags retirés
    assert rows[shared]["space"] == "perso" and rows[shared]["user_note"] == "à relire"
    assert rows[plain]["space"] == "main" and "#journal" in rows[plain]["input_text"]
    assert rows[with_link]["kind"] == "note" and rows[with_link]["space"] == "perso"

    enrich_call = next(c for c in fake_llm["enrich"] if "24 heures" in c["content"])
    assert enrich_call["space"] == "perso" and enrich_call["category"] == "principe"
    card = db.fetchone("select content from chunks where item_id = %s and chunk_index = -1", (principle,))["content"]
    assert "Catégorie : Principe" in card

    # listes, filtres, comptes
    perso = client.get("/api/items", params={"space": "perso"}, headers=AUTH).json()
    assert perso["total"] == 5
    assert client.get("/api/items", params={"space": "main"}, headers=AUTH).json()["total"] == 1
    lessons = client.get("/api/items", params={"space": "perso", "category": "leçon"}, headers=AUTH).json()
    assert {i["id"] for i in lessons["items"]} == {auto, tagged, shared, with_link}   # Claude range les autres
    found = client.get("/api/items", params={"q": "décision importante 24 heures", "space": "perso"}, headers=AUTH).json()
    assert found["items"][0]["id"] == principle and all(i["space"] == "perso" for i in found["items"])
    cats = {c["id"]: c["count"] for c in client.get("/api/categories", headers=AUTH).json()}
    assert cats["principe"] == 1 and cats["lecon"] == 4
    assert client.get("/api/stats", headers=AUTH).json()["by_space"] == {"perso": 5, "main": 1}
    assert client.get("/api/tags", params={"space": "perso"}, headers=AUTH).json()[0]["tag"]
    assert client.get("/api/items", params={"space": "ailleurs"}, headers=AUTH).status_code == 400
    taxo = client.get("/api/taxonomy", headers=AUTH).json()
    assert [s["id"] for s in taxo["spaces"]] == ["main", "perso"] and taxo["categories"][0]["id"] == "principe"


def test_edit_note_and_move(client):
    from app import db

    note = _note(client, title="Sport", content="Courir trois fois par semaine.", category="habitude")
    article = client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non"}, headers=AUTH).json()["id"]
    drain()

    # réécrire une note : le texte change tout de suite, puis elle est retraitée
    r = client.patch(f"/api/items/{note}", json={"content": "Courir quatre fois par semaine, le matin."}, headers=AUTH)
    assert r.json() == {"ok": True, "requeued": True}
    row = db.fetchone("select status, content from items where id = %s", (note,))
    assert row["status"] == "pending" and "quatre" in row["content"]
    assert drain() == 1
    row = db.fetchone("select status, title, content, category from items where id = %s", (note,))
    assert row["status"] == "ready" and row["title"] == "Sport" and row["category"] == "habitude"
    assert "quatre" in db.fetchone(
        "select string_agg(content, ' ') c from chunks where item_id = %s", (note,))["c"]

    # seules les notes se réécrivent ; catégories et espaces validés
    assert client.patch(f"/api/items/{article}", json={"content": "x"}, headers=AUTH).status_code == 400
    assert client.patch(f"/api/items/{note}", json={"content": " "}, headers=AUTH).status_code == 400
    assert client.patch(f"/api/items/{note}", json={"category": "nope"}, headers=AUTH).status_code == 400

    # déplacer un article vers Perso, le catégoriser, corriger son titre (gardé au retraitement)
    r = client.patch(f"/api/items/{article}", json={"space": "perso", "category": "ressource", "title": "Mon titre"},
                     headers=AUTH)
    assert r.json()["requeued"] is False
    client.post(f"/api/items/{article}/reprocess", headers=AUTH)
    drain()
    row = db.fetchone("select space, category, title from items where id = %s", (article,))
    assert (row["space"], row["category"], row["title"]) == ("perso", "ressource", "Mon titre")
    assert client.patch(f"/api/items/{article}", json={"space": "veille"}, headers=AUTH).json()["ok"]
    assert db.fetchone("select space from items where id = %s", (article,))["space"] == "main"
    assert client.patch("/api/items/00000000-0000-0000-0000-000000000000", json={"pinned": True},
                        headers=AUTH).status_code == 404


def test_advice_mode(client, fake_llm):
    # sans charte : le modèle est prévenu
    r = client.post("/api/chat", json={"mode": "advice", "messages": [
        {"role": "user", "content": "Dois-je accepter une promotion qui m'éloigne de ma famille ?"}]}, headers=AUTH)
    assert r.status_code == 200
    assert "aucun principe" in fake_llm["stream"][-1]["messages"][-1]["content"]

    value = _note(client, title="La famille d'abord", content="Ma famille passe avant ma carrière.", category="valeur")
    principle = _note(client, title="Règle des 24 h", content="J'attends 24 heures avant une grosse décision.",
                      category="principe")
    lesson = _note(client, content="Quand j'ai déménagé pour un job, j'ai regretté d'être loin des miens.",
                   category="lecon")
    client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non"}, headers=AUTH)
    drain()
    client.patch(f"/api/items/{principle}", json={"pinned": True}, headers=AUTH)

    r = client.post("/api/chat", json={"mode": "advice", "model": "claude-opus-5-5", "messages": [
        {"role": "user", "content": "Dois-je accepter une promotion qui m'éloigne de ma famille ?"}]}, headers=AUTH)
    events = _events(r)
    assert [e["type"] for e in events][0] == "status" and events[-1]["type"] == "done"
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    ids = [s["id"] for s in sources]
    assert ids[:2] == [principle, value]                 # la charte d'abord, les épinglés en tête
    assert lesson in ids
    call = fake_llm["stream"][-1]
    assert "Mode Conseil" in call["system"] and call["model"] == "claude-opus-5-5"
    prompt = call["messages"][-1]["content"]
    assert "Les sources 1 à 2 sont sa charte" in prompt
    assert "Ma famille passe avant ma carrière." in prompt      # texte intégral des valeurs
    assert "Perso · Valeur (charte)" in prompt
    assert client.post("/api/chat", json={"mode": "magic", "messages": [{"role": "user", "content": "?"}]},
                       headers=AUTH).status_code == 400


def test_mcp_perso_tools(client):
    _note(client, title="Honnêteté", content="Je dis la vérité, même quand elle coûte.", category="valeur")
    _note(client, content="Mentir pour éviter un conflit l'a toujours aggravé.", category="lecon")
    client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non"}, headers=AUTH)
    drain()
    path = "/mcp/mcp-secret"

    tools = {t["name"] for t in _mcp(client, path, "tools/list").json()["result"]["tools"]}
    assert "get_principles" in tools

    text = _mcp(client, path, "tools/call", {"name": "get_principles", "arguments": {}}).json()["result"]["content"][0]["text"]
    assert "## Charte" in text and "Je dis la vérité, même quand elle coûte." in text
    r = _mcp(client, path, "tools/call", {"name": "get_principles",
                                          "arguments": {"situation": "Dois-je cacher une erreur à mon associé ?"}})
    text = r.json()["result"]["content"][0]["text"]
    assert "## Notes et éléments liés" in text and "Mentir pour éviter un conflit" in text

    r = _mcp(client, path, "tools/call", {"name": "search_kb", "arguments": {"query": "vérité", "space": "perso"}})
    text = r.json()["result"]["content"][0]["text"]
    assert "Perso · " in text and "Dire non sans culpabiliser" not in text
    r = _mcp(client, path, "tools/call", {"name": "browse_kb", "arguments": {"space": "perso", "category": "lecon"}})
    assert r.json()["result"]["content"][0]["text"].startswith("1 élément(s)")
    r = _mcp(client, path, "tools/call", {"name": "browse_kb", "arguments": {"space": "lune"}})
    assert "Espace inconnu" in r.json()["result"]["content"][0]["text"]
    r = _mcp(client, path, "tools/call", {"name": "add_to_kb", "arguments": {
        "text": "Objectif : courir un semi-marathon en 2027", "space": "perso", "category": "objectif"}})
    assert r.json()["result"]["content"][0]["text"].startswith("Ajouté")
    r = _mcp(client, path, "tools/call", {"name": "kb_overview", "arguments": {}})
    assert "perso 2" in r.json()["result"]["content"][0]["text"]       # la note ajoutée est encore en traitement


def test_export_by_space_with_files(client):
    from app import db

    _note(client, title="Règle des 24 h", content="J'attends 24 heures avant une grosse décision.", category="principe")
    failed = _note(client, title="Brouillon", content="Une note dont le traitement a échoué.")
    client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non"}, headers=AUTH)
    client.post("/api/ingest", files={"file": ("contrat.txt", b"Contrat de location, signe en 2026.", "text/plain")},
                headers=AUTH)
    drain()
    db.execute("update items set status = 'error' where id = %s", (failed,))

    r = client.get("/api/export", headers=AUTH)
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "KB/Perso/Principes/Règle des 24 h.md" in names
    assert "KB/Veille/Dire non sans culpabiliser.md" in names
    assert "KB/Perso/Leçons/Brouillon.md" in names                 # une note n'est jamais perdue, même en erreur
    md = z.read("KB/Perso/Principes/Règle des 24 h.md").decode()
    assert 'space: "Perso"' in md and 'category: "Principe"' in md
    assert md.split("---\n", 2)[2].lstrip().startswith("J'attends 24 heures")   # le texte de la note d'abord
    assert not any(n.startswith("KB/Fichiers/") for n in names)

    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export", params={"files": True}, headers=AUTH).content))
    stored = [n for n in z.namelist() if n.startswith("KB/Fichiers/")]
    assert len(stored) == 1 and z.read(stored[0]) == b"Contrat de location, signe en 2026."
    doc = next(n for n in z.namelist() if n.startswith("KB/Veille/") and "Dire non" not in n)
    assert "](../Fichiers/" in z.read(doc).decode()


def test_noindex_everywhere(client):
    assert client.get("/api/health").headers["x-robots-tag"] == "noindex, nofollow"
    assert client.get("/").headers["x-robots-tag"] == "noindex, nofollow"


def test_edits_during_processing_are_kept(client, fake_llm, monkeypatch):
    """Une modification faite pendant que le worker traite l'élément ne doit jamais être perdue."""
    from app import db, pipeline

    note = _note(client, content="Première version de la note.", category="reflexion")
    item = db.fetchone("select * from claim_next_item()")
    assert str(item["id"]) == note

    # réécriture pendant le traitement : le résultat périmé est jeté, la nouvelle version est retraitée
    client.patch(f"/api/items/{note}", json={"content": "Deuxième version, la bonne."}, headers=AUTH)
    pipeline.process(item)
    row = db.fetchone("select status, content from items where id = %s", (note,))
    assert row["status"] == "pending" and row["content"] == "Deuxième version, la bonne."
    assert drain() == 1
    row = db.fetchone("select status, content from items where id = %s", (note,))
    assert row["status"] == "ready" and row["content"] == "Deuxième version, la bonne."

    # titre, tags et catégorie changés pendant le traitement : gardés, et la fiche indexée suit
    client.post(f"/api/items/{note}/reprocess", headers=AUTH)
    item = db.fetchone("select * from claim_next_item()")
    client.patch(f"/api/items/{note}", json={"title": "Titre choisi", "tags": ["mien"], "category": "valeur"},
                 headers=AUTH)
    pipeline.process(item)
    row = db.fetchone("select status, title, tags, category, metadata from items where id = %s", (note,))
    assert row["status"] == "ready" and row["title"] == "Titre choisi" and row["category"] == "valeur"
    assert row["tags"][0] == "mien" and row["metadata"]["manual_title"] is True
    card = db.fetchone("select content from chunks where item_id = %s and chunk_index = -1", (note,))["content"]
    assert card.startswith("Titre choisi") and "Catégorie : Valeur" in card

    # un échec tardif ne remet pas en erreur un élément relancé entre-temps
    client.post(f"/api/items/{note}/reprocess", headers=AUTH)
    item = db.fetchone("select * from claim_next_item()")
    client.patch(f"/api/items/{note}", json={"content": "Troisième version."}, headers=AUTH)
    pipeline.fail(item, pipeline.ExtractionError("trop tard"))
    assert db.fetchone("select status from items where id = %s", (note,))["status"] == "pending"


def test_hashtags_respect_explicit_choice_and_formatting(client):
    from app import db

    # espace choisi explicitement : il l'emporte, les hashtags restent dans le texte
    r = client.post("/api/ingest", json={"text": "Idée produit #perso #leçon", "space": "main"}, headers=AUTH)
    row = db.fetchone("select space, category, input_text from items where id = %s", (r.json()["id"],))
    assert (row["space"], row["category"], row["input_text"]) == ("main", None, "Idée produit #perso #leçon")
    # catégorie demandée pour la veille : ignorée
    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/dire-non", "space": "main", "category": "valeur"},
                    headers=AUTH)
    assert db.fetchone("select category from items where id = %s", (r.json()["id"],))["category"] is None
    # mise en forme d'une note conservée (listes imbriquées, code indenté)
    text = "Mes règles :\n- travail\n    - pas de mails le soir #perso\n\tbloc indenté"
    r = client.post("/api/ingest", json={"text": text}, headers=AUTH)
    row = db.fetchone("select space, input_text from items where id = %s", (r.json()["id"],))
    assert row["space"] == "perso"
    assert row["input_text"] == "Mes règles :\n- travail\n    - pas de mails le soir\n\tbloc indenté"
    # déplacer vers la veille retire la catégorie
    note = _note(client, content="Un objectif.", category="objectif")
    client.patch(f"/api/items/{note}", json={"space": "main"}, headers=AUTH)
    assert db.fetchone("select space, category from items where id = %s", (note,)) == {"space": "main", "category": None}


def test_charter_includes_principles_being_reprocessed(client):
    from app import chat, db

    principle = _note(client, title="Règle", content="Je tiens mes promesses.", category="principe")
    drain()
    db.execute("update items set status = 'error' where id = %s", (principle,))
    assert [c["id"] for c in chat.charter_items()] == [principle]
    client.patch(f"/api/items/{principle}", json={"content": "Je tiens mes promesses, même petites."}, headers=AUTH)
    charter = chat.charter_items()
    assert charter[0]["excerpts"] == ["Je tiens mes promesses, même petites."]


def test_place_chosen_in_the_shortcut(client):
    """The Shortcuts send their « Où le ranger ? » answer as `category`: a space or a Perso category."""
    from app import db

    def place(choice, **extra):
        r = client.post("/api/ingest", json={"text": f"Idée {choice}", "category": choice, **extra}, headers=AUTH)
        assert r.status_code == 200, r.text
        return db.fetchone("select space, category from items where id = %s", (r.json()["id"],))

    assert place("Veille") == {"space": "main", "category": None}
    assert place("Perso") == {"space": "perso", "category": None}
    assert place("Leçon") == {"space": "perso", "category": "lecon"}
    assert place("Ressource") == {"space": "perso", "category": "ressource"}
    # the choice is explicit: it wins over hashtags left in the note
    assert place("Veille", note="#perso #principe") == {"space": "main", "category": None}

    # files go through the same path (multipart)
    r = client.post("/api/ingest", data={"category": "Valeur", "note": "à garder"}, headers=AUTH,
                    files={"file": ("valeurs.txt", b"Honnetete et constance.", "text/plain")})
    assert r.status_code == 200, r.text
    assert db.fetchone("select space, category from items where id = %s",
                       (r.json()["id"],)) == {"space": "perso", "category": "valeur"}


def test_errors_carry_a_message_for_the_shortcuts(client, monkeypatch):
    from app import pipeline

    r = client.post("/api/ingest", json={"url": "https://blog.ex.com/x"})
    assert r.status_code == 401 and r.json()["message"] == "Erreur : Jeton invalide ou manquant"
    assert r.json()["detail"] == "Jeton invalide ou manquant"          # the app reads `detail`
    r = client.post("/api/ingest", json={}, headers=AUTH)
    assert r.status_code == 400 and r.json()["message"] == "Erreur : Envoie une URL, un texte ou un fichier"
    r = client.post("/api/notes", json={"space": "perso"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["message"] == "Erreur : requête invalide (content)"
    r = client.post("/api/ingest", json={"text": "x", "category": "inconnue"}, headers=AUTH)
    assert r.status_code == 400 and "Catégorie inconnue" in r.json()["message"]

    def boom(**_):
        raise RuntimeError("Upload Storage échoué (404) : Bucket not found")

    monkeypatch.setattr(pipeline, "ingest", boom)
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/ingest", json={"text": "x"}, headers=AUTH)
    assert r.status_code == 500
    assert r.json()["message"] == "Erreur : erreur serveur (RuntimeError: Upload Storage échoué (404) : Bucket not found)"
