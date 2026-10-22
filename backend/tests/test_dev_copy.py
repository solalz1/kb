"""A dev copy (SETUP.md step 15): the app and the API of a branch, next to the real KB on the same database. It must
never do the real KB's background work, touch the schema, or hold many connections."""

import threading

from app import notion, obsidian
from app.config import Settings, get_settings


def test_a_dev_copy_does_no_background_work(monkeypatch):
    monkeypatch.setenv("DEV_COPY", "true")
    monkeypatch.setenv("RUN_WORKER", "true")
    monkeypatch.setenv("AUTO_MIGRATE", "true")
    s = Settings()
    assert s.dev_copy and not s.run_worker and not s.auto_migrate
    assert s.db_pool_max <= 3


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
