"""Journal: Perso notes of category 'journal', each on a calendar day."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from .conftest import drain
from .test_e2e import AUTH


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _write(client, text, day=None, **extra):
    body = {"content": text, "category": "journal", **({"entry_date": day} if day else {}), **extra}
    r = client.post("/api/notes", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_month_and_day(client):
    from app import db, pipeline

    today = pipeline.today()
    first = _write(client, "Matin calme, bonne séance de sport.")
    _write(client, "Le soir, dîner avec des amis.")
    _write(client, "Une journée à Lyon.", "2026-09-14")
    other = client.post("/api/notes", json={"content": "Un principe", "category": "principe"}, headers=AUTH).json()["id"]
    client.post("/api/ingest", json={"text": "Une idée de veille"}, headers=AUTH)
    drain()

    assert db.fetchone("select entry_date from items where id = %s", (first,))["entry_date"] == today
    assert db.fetchone("select entry_date from items where id = %s", (other,))["entry_date"] is None

    month = client.get("/api/journal", headers=AUTH).json()
    assert month["today"] == today.isoformat() and month["month"] == today.isoformat()[:7]
    assert month["days"].get(today.isoformat()) == 2
    sept = client.get("/api/journal?month=2026-09", headers=AUTH).json()
    assert sept["days"] == {"2026-09-14": 1}
    assert client.get("/api/journal?month=2026-13", headers=AUTH).status_code == 400

    day = client.get(f"/api/journal/{today.isoformat()}", headers=AUTH).json()
    assert [e["text"] for e in day["entries"]] == ["Matin calme, bonne séance de sport.", "Le soir, dîner avec des amis."]
    assert all(e["day"] == today.isoformat() for e in day["entries"])
    assert client.get("/api/journal/pas-une-date", headers=AUTH).status_code == 422
    assert client.get("/api/journal/2026-09-14").status_code == 401


def test_edit_move_and_delete(client):
    note = _write(client, "Brouillon", "2026-10-01")
    drain()
    # rewritten: still listed (the user's own text) while it is processed again
    assert client.patch(f"/api/items/{note}", json={"content": "Texte final"}, headers=AUTH).json()["requeued"]
    assert client.get("/api/journal/2026-10-01", headers=AUTH).json()["entries"][0]["text"] == "Texte final"
    # moved to another day
    client.patch(f"/api/items/{note}", json={"entry_date": "2026-10-02"}, headers=AUTH)
    assert client.get("/api/journal/2026-10-01", headers=AUTH).json()["entries"] == []
    assert len(client.get("/api/journal/2026-10-02", headers=AUTH).json()["entries"]) == 1
    # archived or deleted: gone from the calendar
    client.patch(f"/api/items/{note}", json={"archived": True}, headers=AUTH)
    assert client.get("/api/journal?month=2026-10", headers=AUTH).json()["days"] == {}
    client.patch(f"/api/items/{note}", json={"archived": False}, headers=AUTH)
    client.delete(f"/api/items/{note}", headers=AUTH)
    assert client.get("/api/journal?month=2026-10", headers=AUTH).json()["days"] == {}


def test_notes_filed_in_the_journal_later_keep_their_day(client):
    from app import db, pipeline

    note = client.post("/api/notes", json={"content": "Une réflexion", "category": "reflexion"}, headers=AUTH).json()["id"]
    db.execute("update items set created_at = created_at - interval '3 days' where id = %s", (note,))
    client.patch(f"/api/items/{note}", json={"category": "journal"}, headers=AUTH)
    day = (pipeline.today() - timedelta(days=3)).isoformat()
    assert db.fetchone("select entry_date from items where id = %s", (note,))["entry_date"].isoformat() == day
    # and one filed with a hashtag from a Shortcut, before the calendar existed: the day it was saved
    db.execute("insert into items (kind, input_text, space, category, status) values ('note', 'Ancien', 'perso', 'journal', 'ready')")
    assert client.get(f"/api/journal/{pipeline.today().isoformat()}", headers=AUTH).json()["entries"][0]["text"] == "Ancien"


def test_today_follows_the_user_timezone(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import pipeline
    from app.config import get_settings

    for tz in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "Europe/Paris"):      # UTC+14, UTC-11, the default
        monkeypatch.setattr(get_settings(), "digest_timezone", tz)
        assert pipeline.today() == datetime.now(ZoneInfo(tz)).date()
        assert isinstance(pipeline.today(), date)


def test_filed_in_the_journal_on_a_chosen_day(client):
    from app import db

    note = client.post("/api/notes", json={"content": "Un souvenir", "category": "reflexion"}, headers=AUTH).json()["id"]
    r = client.patch(f"/api/items/{note}", json={"category": "journal", "entry_date": "2026-09-01"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert db.fetchone("select entry_date from items where id = %s", (note,))["entry_date"] == date(2026, 9, 1)
    for bad in ("9999-12", "0000-01", "2026-1"):
        assert client.get(f"/api/journal?month={bad}", headers=AUTH).status_code == 400, bad


def _days_ago(n: int) -> str:
    from app import pipeline

    return (pipeline.today() - timedelta(days=n)).isoformat()


def _sse(r) -> list[dict]:
    import json

    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_shared_into_the_journal_on_a_chosen_day(client):
    """The « + » in Journal mode: a link, a file or a text lands on the day chosen (today by default)."""
    from app import db, pipeline

    def row(r):
        assert r.status_code == 200, r.text
        return db.fetchone("select space, category, entry_date, kind from items where id = %s", (r.json()["id"],))

    link = row(client.post("/api/ingest", json={"url": "https://blog.ex.com/marche", "space": "perso",
                                                 "category": "journal", "entry_date": "2026-09-03"}, headers=AUTH))
    assert link == {"space": "perso", "category": "journal", "entry_date": date(2026, 9, 3), "kind": "article"}
    text = row(client.post("/api/ingest", json={"text": "Une pensée du soir", "space": "perso", "category": "journal",
                                                 "entry_date": "2026-09-04"}, headers=AUTH))
    assert text["entry_date"] == date(2026, 9, 4) and text["kind"] == "note"
    upload = row(client.post("/api/ingest", data={"space": "perso", "category": "journal", "entry_date": "2026-09-05"},
                             files={"file": ("photo.png", b"\x89PNG fake", "image/png")}, headers=AUTH))
    assert upload["entry_date"] == date(2026, 9, 5) and upload["category"] == "journal"
    # no day given: today; another category: no day at all
    assert row(client.post("/api/ingest", json={"text": "Maintenant", "space": "perso", "category": "journal"},
                           headers=AUTH))["entry_date"] == pipeline.today()
    assert row(client.post("/api/ingest", json={"text": "Une leçon", "space": "perso", "category": "lecon",
                                                 "entry_date": "2026-09-05"}, headers=AUTH))["entry_date"] is None
    bad = client.post("/api/ingest", json={"text": "?", "space": "perso", "category": "journal", "entry_date": "hier"},
                      headers=AUTH)
    assert bad.status_code == 400 and "AAAA-MM-JJ" in bad.json()["detail"]
    # a link already in Veille, shared again into the journal: it moves there, on the day chosen
    saved = client.post("/api/ingest", json={"url": "https://blog.ex.com/deja"}, headers=AUTH).json()["id"]
    again = client.post("/api/ingest", json={"url": "https://blog.ex.com/deja", "space": "perso", "category": "journal",
                                              "entry_date": "2026-09-06"}, headers=AUTH)
    assert again.json()["id"] == saved
    assert row(again) == {"space": "perso", "category": "journal", "entry_date": date(2026, 9, 6), "kind": "article"}
    assert [e["kind"] for e in client.get("/api/journal/2026-09-05", headers=AUTH).json()["entries"]] == [None]


def test_recent_journal_newest_first_within_a_budget(client):
    from app import journal, pipeline

    _write(client, "Il y a vingt jours.", _days_ago(20))
    _write(client, "Il y a treize jours.", _days_ago(13))
    _write(client, "Avant-hier : " + "long " * 400, _days_ago(2))
    _write(client, "Ce matin.")
    _write(client, "Plus tard ce jour-là.")
    _write(client, "Demain, je prévois.", _days_ago(-1))         # a plan for tomorrow isn't what happened
    drain()
    rows = journal.recent(pipeline.today(), budget=1_000)
    assert [r["excerpts"][0][:13] if r["excerpts"] else None for r in rows] == [
        "Plus tard ce ", "Ce matin.", None, "Il y a treize"]          # past the budget: the summary alone
    assert rows[0]["entry_date"] == pipeline.today() and rows[-1]["entry_date"].isoformat() == _days_ago(13)
    assert journal.label(date(2026, 11, 19)) == "Journal du jeudi 19 novembre 2026"


def test_advice_reads_the_recent_journal(client, fake_llm):
    from app import journal, pipeline

    client.post("/api/notes", json={"content": "Je dors avant de décider.", "category": "principe"}, headers=AUTH)
    _write(client, "Journée lourde : la proposition de Lyon me travaille encore.")
    _write(client, "Dîner avec Paul, il trouve que je m'épuise.", _days_ago(4))
    _write(client, "J'ai déjà refusé une mutation à Lyon et je l'ai regretté.", _days_ago(200))
    client.post("/api/notes", json={"content": "Une leçon sur le travail.", "category": "lecon"}, headers=AUTH)
    drain()

    r = client.post("/api/chat", json={"mode": "advice", "messages": [
        {"role": "user", "content": "Dois-je accepter la mutation à Lyon ?"}]}, headers=AUTH)
    events = _sse(r)
    assert events[0] == {"type": "status", "text": "Je relis tes principes, ton journal et tes notes…"}
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    days = [s["entry_date"] for s in sources]
    assert days[:3] == [None, pipeline.today().isoformat(), _days_ago(4)]     # the charter, then the last days
    assert _days_ago(200) in days                                             # an older page found by the search
    assert all(d is None for s, d in zip(sources, days) if s["category"] != "journal")

    prompt = fake_llm["stream"][-1]["messages"][-1]["content"]
    assert "La source 1 est sa charte" in prompt
    assert "Les sources 2 à 3 sont son journal des 14 derniers jours, du plus récent au plus ancien." in prompt
    assert f"Infos : Perso · {journal.label(pipeline.today())}" in prompt
    assert "« Journée lourde : la proposition de Lyon me travaille encore. »" in prompt     # in full
    assert prompt.index("Journée lourde") < prompt.index("Dîner avec Paul") < prompt.index("déjà refusé")
    system = fake_llm["stream"][-1]["system"]
    assert "## Ce que dit ton journal" in system and "ne dit rien d'utile" in system


def test_advice_without_a_recent_journal(client, fake_llm):
    client.post("/api/notes", json={"content": "Je dors avant de décider.", "category": "principe"}, headers=AUTH)
    _write(client, "Un vieux souvenir.", _days_ago(30))
    drain()
    client.post("/api/chat", json={"mode": "advice", "messages": [{"role": "user", "content": "Que faire ?"}]},
                headers=AUTH)
    prompt = fake_llm["stream"][-1]["messages"][-1]["content"]
    assert "Il n'a rien écrit dans son journal ces 14 derniers jours." in prompt
    assert "Un vieux souvenir" in prompt                       # still found by the search, with its date


def _tool(client, name, **arguments):
    from .test_e2e import _mcp

    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]["content"][0]["text"]


def test_the_connector_reads_the_journal(client):
    from app import journal, pipeline
    from .test_e2e import _mcp

    today = pipeline.today()
    _write(client, "Ce matin, envie de changer de rythme.")
    _write(client, "Hier soir, appel avec maman.", _days_ago(1))
    _write(client, "Une semaine plus tôt.", _days_ago(10))
    _write(client, "Au printemps.", "2026-04-02")
    client.post("/api/notes", json={"content": "Je dors avant de décider.", "category": "principe"}, headers=AUTH)
    drain()

    init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
    instructions = _mcp(client, "/mcp/mcp-secret", "initialize", init).json()["result"]["instructions"]
    assert "get_journal" in instructions and "14 derniers jours" in instructions
    tools = _mcp(client, "/mcp/mcp-secret", "tools/list", rid=2).json()["result"]["tools"]
    assert "get_journal" in {t["name"] for t in tools}

    week = _tool(client, "get_journal")
    assert week.startswith(f"Aujourd'hui : {today.isoformat()}.")
    assert "Ce matin, envie de changer de rythme." in week and "Hier soir, appel avec maman." in week
    assert "Une semaine plus tôt" not in week
    assert week.index(f"## {journal.label(today - timedelta(days=1))}") < week.index(f"## {journal.label(today)}")

    day = _tool(client, "get_journal", day="2026-04-02")
    assert "## Journal du jeudi 2 avril 2026 (2026-04-02)" in day and "Au printemps." in day
    assert "Rien d'écrit" in _tool(client, "get_journal", day="2026-04-03")
    period = _tool(client, "get_journal", start=_days_ago(12), end=_days_ago(1))
    assert "Une semaine plus tôt." in period and "Ce matin" not in period
    capped = _tool(client, "get_journal", start="2026-01-01", end=today.isoformat())
    assert "ramenée aux 90 derniers jours" in capped and "Au printemps" not in capped
    assert "invalide" in _tool(client, "get_journal", day="le 3 novembre")

    advice = _tool(client, "get_principles")
    assert advice.index("## Charte") < advice.index("## Journal récent (14 derniers jours")
    assert "Contenu complet :\nCe matin, envie de changer de rythme." in advice
    assert "Une semaine plus tôt." in advice and "Au printemps" not in advice
    assert "## Journal récent" in _tool(client, "get_principles", situation="Changer de rythme de travail ?")

    listed = _tool(client, "browse_kb", space="perso", category="journal")
    assert "(Journal du jeudi 2 avril 2026" in listed
    found = _tool(client, "search_kb", query="printemps", space="perso")
    assert "Perso · Journal du jeudi 2 avril 2026" in found


def test_on_this_day(client):
    """« Ce jour-là »: the same day 1, 3, 6 months and 1 to 10 years before, only the dates with something."""
    from app import journal

    assert journal.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)      # no 31 February: its last day
    assert journal.months_before(date(2028, 3, 31), 1) == date(2028, 2, 29)
    assert journal.months_before(date(2026, 1, 15), 3) == date(2025, 10, 15)
    assert journal.months_before(date(2026, 11, 21), 120) == date(2016, 11, 21)
    assert [journal.ago(n) for n in (1, 3, 12, 24)] == ["il y a 1 mois", "il y a 3 mois", "il y a 1 an", "il y a 2 ans"]

    _write(client, "Aujourd'hui.", "2026-11-21")
    _write(client, "Un mois avant.", "2026-10-21")
    _write(client, "Un an avant, le matin.", "2025-11-21")
    _write(client, "Un an avant, le soir.", "2025-11-21")
    _write(client, "Cinq ans avant.", "2021-11-21")
    _write(client, "Deux mois avant : pas un rendez-vous.", "2026-09-21")
    _write(client, "Onze ans avant : trop loin.", "2015-11-21")
    body = client.get("/api/journal/2026-11-21", headers=AUTH).json()
    assert [e["text"] for e in body["entries"]] == ["Aujourd'hui."]
    echoes = body["echoes"]
    assert [(e["months"], e["date"]) for e in echoes] == [(1, "2026-10-21"), (12, "2025-11-21"), (60, "2021-11-21")]
    assert [e["text"] for e in echoes[1]["entries"]] == ["Un an avant, le matin.", "Un an avant, le soir."]
    assert client.get("/api/journal/2026-11-22", headers=AUTH).json()["echoes"] == []

    from .test_e2e import _mcp

    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": "get_journal", "arguments": {"day": "2026-11-21"}})
    text = r.json()["result"]["content"][0]["text"]
    assert "## Ce jour-là" in text
    assert text.index("### Il y a 1 mois · Journal du mercredi 21 octobre 2026") < text.index("### Il y a 1 an · ")
    assert "Cinq ans avant." in text and "trop loin" not in text and "pas un rendez-vous" not in text
