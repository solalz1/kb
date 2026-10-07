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
