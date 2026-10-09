"""The app applies its schema when it starts, so a deploy never runs code ahead of its database."""

import threading

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import ADMIN_DB, TEST_DB
from .test_e2e import AUTH

OLD_DB = TEST_DB.replace("/kb_test", "/kb_migrate_test")


@pytest.fixture
def old_database(database):
    """A database on an older schema: the columns added by the latest PRs are missing, as in production."""
    from app import migrate

    with psycopg.connect(ADMIN_DB, autocommit=True) as c:
        c.execute("drop database if exists kb_migrate_test with (force)")
        c.execute("create database kb_migrate_test")
    migrate.run(OLD_DB)
    with psycopg.connect(OLD_DB, autocommit=True) as c:
        c.execute("alter table items drop column translations, drop column entry_date")
        c.execute("insert into items (kind, title, status) values ('note', 'Une note d''avant', 'ready')")
    yield OLD_DB
    with psycopg.connect(ADMIN_DB, autocommit=True) as c:
        c.execute("drop database if exists kb_migrate_test with (force)")


def _columns(url):
    with psycopg.connect(url) as c:
        return {r[0] for r in c.execute(
            "select column_name from information_schema.columns where table_schema = 'public' and table_name = 'items'")}


def test_brings_an_old_database_up_to_date(old_database):
    from app import migrate

    assert {"translations", "entry_date"} - _columns(old_database) == {"translations", "entry_date"}
    assert migrate.run(old_database) == ["20261002000000_init.sql"]
    assert {"translations", "entry_date"} <= _columns(old_database)
    with psycopg.connect(old_database) as c:
        assert c.execute("select title, translations from items").fetchall() == [("Une note d'avant", {})]


def test_running_again_changes_nothing(clean_db):
    from app import db, migrate

    db.execute("insert into items (kind, title, status, translations) values ('note', 'Gardée', 'ready', %s)",
               (db.jsonb({"en": {"title": "Kept"}}),))
    migrate.run()
    migrate.run()
    assert db.fetchone("select title, translations from items") == {"title": "Gardée",
                                                                      "translations": {"en": {"title": "Kept"}}}


def test_two_containers_starting_together(old_database):
    """During a deploy the old and the new container may both start: the advisory lock runs them one after the other."""
    from app import migrate

    errors = []

    def go():
        try:
            migrate.run(old_database)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert "entry_date" in _columns(old_database)


def test_startup_status(database, monkeypatch):
    from app import migrate
    from app.config import get_settings

    monkeypatch.setattr(migrate, "status", "not run")     # restored after the test
    s = get_settings()
    monkeypatch.setattr(s, "auto_migrate", False)
    assert migrate.run_at_startup() == "off"

    monkeypatch.setattr(s, "auto_migrate", True)
    assert migrate.run_at_startup() == "ok"

    # a broken database address never stops the app from starting: the error is reported instead
    monkeypatch.setattr(s, "database_url", "postgresql://nobody@localhost:1/nothing?connect_timeout=1")
    status = migrate.run_at_startup()
    assert status.startswith("error: ")


def test_health_reports_the_schema(clean_db, monkeypatch):
    from app import migrate
    from app.config import get_settings
    from app.main import app

    monkeypatch.setattr(migrate, "status", "not run")     # restored after the test
    monkeypatch.setattr(get_settings(), "auto_migrate", True)
    with TestClient(app) as client:      # the app's start applies the schema
        body = client.get("/api/health", headers=AUTH).json()
    assert body["schema"] == "ok"
    assert body["ok"] is True


def test_repairs_lists_saved_as_text(clean_db):
    """A card's lists once came back as one "<item>…</item>" text: the item page crashed on them. Startup repairs
    them, and leaves healthy rows alone."""
    from app import db, migrate

    broken = "\n<item>Les questions comptent.</item>\n<item>Trois débats d'experts.</item>\n</item>\n</invoke>"
    db.execute("""insert into items (kind, title, status, key_points, use_cases, entities)
                  values ('tweet', 'Cassée', 'ready', %s, %s, 'null'::jsonb),
                         ('tweet', 'Saine', 'ready', '["Un point"]', '["Un usage"]', '[{"name": "X", "type": "product"}]')""",
               (db.jsonb(broken), db.jsonb("- Utile pour réviser\n- Utile avant un examen\n</invoke>")))
    migrate.run()
    rows = {r["title"]: r for r in db.fetchall("select title, key_points, use_cases, entities from items")}
    assert rows["Cassée"]["key_points"] == ["Les questions comptent.", "Trois débats d'experts."]
    assert rows["Cassée"]["use_cases"] == ["Utile pour réviser", "Utile avant un examen"]
    assert rows["Cassée"]["entities"] == []
    assert rows["Saine"] == {"title": "Saine", "key_points": ["Un point"], "use_cases": ["Un usage"],
                             "entities": [{"name": "X", "type": "product"}]}
    migrate.run()
    assert db.fetchone("select key_points from items where title = 'Cassée'")["key_points"] == [
        "Les questions comptent.", "Trois débats d'experts."]
