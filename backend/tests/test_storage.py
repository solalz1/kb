"""Storage health: /api/health says why file shares would fail (wrong key, missing bucket, unreachable URL)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import storage
from app.config import get_settings


@pytest.fixture
def supabase(monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_url", "https://ref.supabase.example")
    monkeypatch.setattr(get_settings(), "supabase_service_key", "sb_secret_test")
    monkeypatch.setattr(storage, "_check_cache", None)
    calls = []

    def answer(response):
        def get(url, headers=None, timeout=None):
            calls.append((url, headers))
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(storage.httpx, "get", get)

    answer.calls = calls
    return answer


def test_local_storage_when_supabase_is_not_configured(database):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True, "db": True, "auth_configured": True, "schema": "off",
                                               "storage": "local"}


def test_check_reports_what_storage_answered(supabase):
    supabase(httpx.Response(200, json={"id": "kb-files"}))
    assert storage.check() == "ok"
    url, headers = supabase.calls[0]
    assert url == "https://ref.supabase.example/storage/v1/bucket/kb-files"
    assert headers == {"apikey": "sb_secret_test"}       # new secret keys are not JWTs: no Bearer header

    storage._check_cache = None
    supabase(httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found"}))
    assert storage.check().startswith("erreur 400 : ") and "Bucket not found" in storage.check()

    storage._check_cache = None
    supabase(httpx.ConnectTimeout("timed out"))
    assert storage.check() == "injoignable : ConnectTimeout"


def test_check_is_cached(supabase):
    supabase(httpx.Response(200))
    storage.check()
    storage.check()
    assert len(supabase.calls) == 1
