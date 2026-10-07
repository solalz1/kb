"""Configuration des tests : base Postgres dédiée, embeddings factices, Claude simulé."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

TEST_DB = os.environ.get("KB_TEST_DATABASE_URL", "postgresql://postgres@localhost:5433/kb_test?host=/tmp")
ADMIN_DB = TEST_DB.replace("/kb_test", "/postgres")

os.environ.update({
    "DATABASE_URL": TEST_DB,
    "KB_API_TOKEN": "test-token",
    "KB_MCP_SECRET": "mcp-secret",
    "EMBEDDINGS_PROVIDER": "fake",
    "ANTHROPIC_API_KEY": "sk-test",
    "RUN_WORKER": "false",
    "AUTO_MIGRATE": "false",     # the session fixture applies the schema; tests/test_migrate.py covers startup
    "LOCAL_STORAGE_DIR": tempfile.mkdtemp(prefix="kb-files-"),
    "SUPABASE_URL": "",
    "SUPABASE_SERVICE_KEY": "",
    "X_BEARER_TOKEN": "x-test",
    "LINK_MIN_SIMILARITY": "0.05",
    "PUBLIC_BASE_URL": "https://kb.example.com",
    "STATIC_DIR": "/nonexistent",
})

MIGRATION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261002000000_init.sql"


@pytest.fixture(scope="session")
def database():
    import psycopg

    with psycopg.connect(ADMIN_DB, autocommit=True) as c:
        c.execute("drop database if exists kb_test with (force)")
        c.execute("create database kb_test")
    subprocess.run(["psql", TEST_DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(MIGRATION)], check=True,
                   capture_output=True)
    yield TEST_DB
    from app import db

    db.close()


@pytest.fixture
def clean_db(database):
    from app import db

    db.execute("truncate items, chunks, item_links, actions, kb_settings, notion_trash, watch, digests, digest_feedback, usage_log "
               "restart identity cascade")
    yield


@pytest.fixture(autouse=True)
def billing_apis(monkeypatch):
    """The services' billing APIs (Claude Console costs, X credits) are never called from tests: a test puts the answer
    for a URL in `.answers` (a dict, or a function of the query parameters); any other URL fails like a service that
    doesn't answer. `.calls` lists (url, headers, params)."""
    from types import SimpleNamespace

    from app import costs

    fake = SimpleNamespace(answers={}, calls=[])

    def get(url, headers, params=None):
        fake.calls.append((url, headers, params or {}))
        answer = fake.answers.get(url)
        if answer is None:
            raise RuntimeError("503 : no billing API in tests")
        return answer(params or {}) if callable(answer) else answer

    monkeypatch.setattr(costs, "_http_get", get)
    costs._cache.clear()
    yield fake
    costs._cache.clear()


@pytest.fixture
def fake_llm(monkeypatch):
    """Remplace tous les appels à Claude par des réponses déterministes."""
    from app import llm

    calls = {"enrich": [], "describe_image": [], "links": [], "stream": []}

    def enrich(**kw):
        calls["enrich"].append(kw)
        words = [w.strip(".,:;!?«»()").lower() for w in (kw["content"] or "").split()]
        tags = sorted({w for w in words if len(w) > 6})[:4] or ["divers"]
        return {
            "title": f"Titre généré : {(kw['content'] or '')[:30]}",
            "summary": f"Résumé de {kw['kind']} : {(kw['content'] or '')[:200]}",
            "key_points": ["Point A", "Point B"],
            "tags": tags,
            "entities": [{"name": "Andrej Karpathy", "type": "person"}],
            "use_cases": ["Utile pour tester la KB"],
            "action_items": [{"text": "Tester l'outil mentionné", "kind": "try"}],
            "genre": "other",
            "language": "fr",
            **({"category": "lecon"} if kw.get("space") == "perso" else {}),
            "translations": {"en": {"title": f"Generated title: {(kw['content'] or '')[:30]}",
                                    "summary": f"Summary of the {kw['kind']}: {(kw['content'] or '')[:200]}",
                                    "key_points": ["Point A (en)", "Point B (en)"],
                                    "use_cases": ["Useful to test the KB"]}},
        }

    def describe_image(data, media_type=None, context=""):
        calls["describe_image"].append(context)
        return {"title": "Capture d'écran", "description": "Une capture d'un tweet sur les agents.",
                "text_in_image": "Les agents RAG changent tout", "source_author": "@someone",
                "source_platform": "X", "source_url": ""}

    def explain_links(item, candidates):
        calls["links"].append(candidates)
        return [{"id": c["id"], "related": True, "reason": "Même sujet"} for c in candidates]

    def stream_text(*, system, messages, model=None, max_tokens=4000):
        calls["stream"].append({"system": system, "messages": messages, "model": model})
        yield "Réponse "
        yield "sourcée [1]."

    monkeypatch.setattr(llm, "enrich", enrich)
    monkeypatch.setattr(llm, "describe_image", describe_image)
    monkeypatch.setattr(llm, "describe_frames", lambda frames, context="": "Des diapositives sur l'IA.")
    monkeypatch.setattr(llm, "explain_links", explain_links)
    monkeypatch.setattr(llm, "rewrite_query", lambda history, q: q + " (reformulée)")
    monkeypatch.setattr(llm, "plan_project_queries",
                        lambda d: {"project_summary": "Projet test", "queries": ["agents rag", "évaluation"]})
    monkeypatch.setattr(llm, "stream_text", stream_text)
    monkeypatch.setattr(llm, "transcribe_pdf", lambda data, first_page=1: f"[p. {first_page}]\nTexte OCR du scan")
    return calls


def drain():
    from app import worker

    n = 0
    while worker.run_once():
        n += 1
    return n
