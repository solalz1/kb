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

MIGRATIONS = sorted((Path(__file__).resolve().parents[2] / "supabase" / "migrations").glob("*.sql"))


@pytest.fixture(scope="session")
def database():
    import psycopg

    with psycopg.connect(ADMIN_DB, autocommit=True) as c:
        c.execute("drop database if exists kb_test with (force)")
        c.execute("create database kb_test")
    for migration in MIGRATIONS:
        subprocess.run(["psql", TEST_DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(migration)], check=True,
                       capture_output=True)
    yield TEST_DB
    from app import db

    db.close()


@pytest.fixture
def clean_db(database):
    from app import db

    db.execute("truncate items, chunks, item_links, actions, kb_settings, notion_trash, watch, digests, digest_feedback, usage_log, "
               "folders, wiki_pages, wiki_page_sources, wiki_revisions, wiki_log, wiki_jobs restart identity cascade")
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


@pytest.fixture(autouse=True)
def no_browser_fetch(monkeypatch):
    """Reading a page "as a browser" (curl_cffi) never leaves the tests: a test fakes its answer when it needs one."""
    from app.extractors import web

    monkeypatch.setattr(web, "_browser_get", lambda url: None)


@pytest.fixture
def fake_llm(monkeypatch):
    """Remplace tous les appels à Claude par des réponses déterministes."""
    from app import llm

    calls = {"enrich": [], "describe_image": [], "links": [], "stream": [], "classify": []}

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
            # the first folder whose name the content mentions
            "folder": next((f["name"] for f in kw.get("folders") or [] if _mentions(kw["content"], f["name"])), None),
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

    def classify_folders(items, folders):
        calls["classify"].append([it["id"] for it in items])
        text = lambda it: f"{it.get('title') or ''} {it.get('summary') or ''} {' '.join(it.get('tags') or [])}"
        return {it["id"]: f["name"] for it in items for f in reversed(folders) if _mentions(text(it), f["name"])}

    def stream_text(*, system, messages, model=None, max_tokens=4000):
        calls["stream"].append({"system": system, "messages": messages, "model": model})
        yield "Réponse "
        yield "sourcée [1]."

    monkeypatch.setattr(llm, "enrich", enrich)
    monkeypatch.setattr(llm, "describe_image", describe_image)
    monkeypatch.setattr(llm, "describe_frames", lambda frames, context="": "Des diapositives sur l'IA.")
    monkeypatch.setattr(llm, "explain_links", explain_links)
    monkeypatch.setattr(llm, "classify_folders", classify_folders)
    monkeypatch.setattr(llm, "rewrite_query", lambda history, q: q + " (reformulée)")
    monkeypatch.setattr(llm, "plan_project_queries",
                        lambda d: {"project_summary": "Projet test", "queries": ["agents rag", "évaluation"]})
    monkeypatch.setattr(llm, "stream_text", stream_text)
    monkeypatch.setattr(llm, "transcribe_pdf", lambda data, first_page=1: f"[p. {first_page}]\nTexte OCR du scan")
    _fake_wiki(monkeypatch, calls)
    return calls


# Pages the fake wiki files a source into, by a word of the source: themes shared by many items, never one per item
WIKI_TOPICS = [("agent", "Agents IA", "research", "theme"), ("benchmark", "Évaluation des agents", "research", "theme"),
               ("promesse", "Mes principes", "personal", "theme"), ("journée", "Journal de bord", "personal", "theme")]


def _fake_wiki(monkeypatch, calls):
    """The wiki's Claude calls, deterministic: each source goes to the topic pages its words name (WIKI_TOPICS) and
    to the page of the person the fake enrichment always finds; a page gets one section per source."""
    import re

    from app import llm

    calls.update(wiki_plan=[], wiki_write=[], wiki_lint=[], wiki_pick=[], wiki_answer=[])

    def titles_of(index):
        return re.findall(r"^- (.+?) \[", index or "", re.M)

    def plan(*, source, index, schema_text, max_pages, model=None):
        calls["wiki_plan"].append({"source": source, "index": index, "schema": schema_text})
        if "lien mort" in source:
            return {"takeaways": [], "pages": []}
        known = {t.lower() for t in titles_of(index)}
        text = source.lower()
        pages = [{"title": title, "section": section, "kind": kind, "why": f"parle de {word}",
                  "action": "update" if title.lower() in known else "create"}
                 for word, title, section, kind in WIKI_TOPICS if word in text]
        pages.append({"title": "Andrej Karpathy", "section": "research", "kind": "entity", "why": "cité",
                      "action": "update" if "andrej karpathy" in known else "create"})
        return {"takeaways": ["Ce que la source apporte"], "pages": pages[:max_pages]}

    def write(*, source, ref, pages, titles, schema_text, model=None):
        calls["wiki_write"].append({"ref": ref, "pages": [p["title"] for p in pages], "titles": titles})
        first = re.search(r"^Titre : (.*)$", source, re.M)
        out = []
        for p in pages:
            others = [q["title"] for q in pages if q["title"] != p["title"]]
            link = f" Voir aussi [[{others[0]}]]." if others else ""
            body = (p.get("content") or f"Synthèse sur {p['title']}.").rstrip()
            out.append({"title": p["title"], "section": p["section"], "kind": p["kind"],
                        "summary": f"Ce que le wiki sait de {p['title']}",
                        "content": f"{body}\n\n## {first.group(1) if first else 'Source'}\nUn fait [src:{ref}].{link}"})
        return {"pages": out}

    def lint(*, pages, findings, titles, sources, schema_text, model=None):
        calls["wiki_lint"].append({"pages": [p["title"] for p in pages], "findings": findings,
                                   "lost": {p["title"]: p["lost_update"] for p in pages if p.get("lost_update")}})
        fixes = [{"title": p["title"], "section": p["section"], "kind": p["kind"], "summary": "Relue",
                  "content": p["content"] + "\n\nRelu cette nuit.", "reason": "Contradiction signalée"}
                 for p in pages[:1]]
        merges = [{"from": p["title"], "into": p["title"].replace(" (doublon)", ""), "reason": "Même sujet"}
                  for p in pages if p["title"].endswith(" (doublon)")]
        return {"pages": fixes, "merges": merges,
                "suggestions": [{"text": "Creuser l'évaluation des agents sur de vraies tâches", "kind": "question"}]}

    def pick(*, question, index, limit=8):
        calls["wiki_pick"].append(question)
        words = {w.lower() for w in re.findall(r"\w{4,}", question)}
        found = [t for t in titles_of(index) if words & {w.lower() for w in re.findall(r"\w{4,}", t)}]
        return found[:limit]

    def file_answer(*, question, answer, index):
        calls["wiki_answer"].append(question)
        return {"title": f"Réponse : {question[:40]}", "section": "research", "summary": "Une réponse gardée"}

    monkeypatch.setattr(llm, "wiki_plan", plan)
    monkeypatch.setattr(llm, "wiki_write", write)
    monkeypatch.setattr(llm, "wiki_lint", lint)
    monkeypatch.setattr(llm, "wiki_pick", pick)
    monkeypatch.setattr(llm, "wiki_file_answer", file_answer)


def _mentions(text: str | None, name: str) -> bool:
    import re

    return bool(re.search(rf"\b{re.escape(name)}\b", text or "", re.I))


def drain():
    from app import worker

    n = 0
    while worker.run_once():
        n += 1
    return n
