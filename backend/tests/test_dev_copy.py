"""A dev copy (SETUP.md step 15): the app and the API of a branch, next to the real KB on the same database. It must
never do the real KB's background work, touch the schema, or hold many connections."""

import threading

from app import notion, obsidian
from app.config import Settings, get_settings

from .test_e2e import client  # noqa: F401 — the API with the test database


def test_a_dev_copy_does_no_background_work(monkeypatch):
    monkeypatch.setenv("DEV_COPY", "true")
    monkeypatch.setenv("RUN_WORKER", "true")
    monkeypatch.setenv("AUTO_MIGRATE", "true")
    s = Settings()
    assert s.dev_copy and not s.run_worker and not s.auto_migrate
    assert s.db_pool_max <= 5


def test_a_railway_service_named_dev_is_a_copy_even_without_its_variables(monkeypatch):
    monkeypatch.delenv("DEV_COPY", raising=False)
    monkeypatch.setenv("RUN_WORKER", "true")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "kb-dev")
    assert Settings().dev_copy is True
    assert Settings().run_worker is False
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "kb")
    s = Settings()
    assert s.dev_copy is False and s.run_worker is True and s.db_pool_max == 10


def test_sync_now_on_a_copy_leaves_it_to_the_real_kb(monkeypatch):
    """« Synchroniser maintenant » in a copy: no pass in this process (the real KB's syncers would race it)."""
    monkeypatch.setattr(get_settings(), "dev_copy", True)
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    notion.request_sync()
    obsidian.request_sync()
    assert started == []


def test_jobs_and_syncs_from_a_copy_run_on_the_real_kb(client, monkeypatch):
    """On a copy, a digest to write or a sync is asked of the real KB (which outlives the copy's redeploys), once."""
    from app import main

    from .test_e2e import AUTH

    s = get_settings()
    monkeypatch.setattr(s, "dev_copy", True)
    monkeypatch.setattr(s, "public_base_url", "https://kb.example.com")
    monkeypatch.setattr(s, "notion_token", "ntn_test")
    monkeypatch.setattr(s, "notion_parent_page_id", "0123456789abcdef0123456789abcdef")
    sent = []

    class Answer:
        status_code = 200

        def json(self):
            return {"ok": True, "id": 42}

    monkeypatch.setattr(main.httpx, "post", lambda url, **kw: sent.append((url, kw["headers"].get("x-kb-forwarded"))) or Answer())
    monkeypatch.setattr(notion, "request_sync", lambda: (_ for _ in ()).throw(AssertionError("no pass in the copy")))
    assert client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH).json() == {"ok": True, "id": 42}
    assert client.post("/api/notion/sync", headers=AUTH).json()["ok"] is True
    assert sent == [("https://kb.example.com/api/digests/generate", "1"), ("https://kb.example.com/api/notion/sync", "1")]
    # a request the real KB forwarded to itself (PUBLIC_BASE_URL pointing at the copy) runs here, never in a loop
    monkeypatch.setattr(notion, "request_sync", lambda: None)
    assert client.post("/api/notion/sync", headers={**AUTH, "x-kb-forwarded": "1"}).json()["ok"] is True
    assert len(sent) == 2
    assert client.get("/api/health").json()["real_url"] == "https://kb.example.com"
