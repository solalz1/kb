"""Tech digest agent: sources, learned interests, daily and weekly digests, projects, feedback, scheduling.

Every external service is faked: HTTP sources by a URL router, X by a fake search, Claude by a tool dispatcher.
"""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.config import get_settings
from app.digest import agent, profile, render, sources
from app.extractors import twitter

from .conftest import drain
from .test_e2e import AUTH, _mcp
from .test_llm import FakeClaude as FakeClaudeAPI, _bad_request, REFUSED

REAL_CALL_TOOL = llm.call_tool          # the fixtures replace it; one test needs the real one

NOW = datetime.now(timezone.utc)
RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Techmeme</title>
<item><title>Un labo publie un modèle ouvert de 1T paramètres</title><link>https://news.ex.com/open-1t?utm_source=rss</link>
<description>&lt;p&gt;Poids ouverts, licence Apache.&lt;/p&gt;</description>
<pubDate>{(NOW - timedelta(hours=3)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>Vieille nouvelle</title><link>https://news.ex.com/old</link>
<pubDate>{(NOW - timedelta(days=9)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
</channel></rss>"""


def fake_get_json(url, params=None, headers=None):
    if "hn.algolia.com" in url:
        return {"hits": [
            {"objectID": "1", "title": "Show HN: un profileur GPU minimaliste", "url": "https://gpu.ex.com/profiler",
             "points": 420, "num_comments": 80, "created_at_i": int(NOW.timestamp()) - 3600, "author": "dev1"},
            {"objectID": "2", "title": "Un labo publie un modèle ouvert", "url": "https://news.ex.com/open-1t",
             "points": 300, "num_comments": 50, "created_at_i": int(NOW.timestamp()) - 7200, "author": "dev2"},
        ]}
    if "huggingface.co/api/daily_papers" in url:
        return [{"paper": {"id": "2610.00001", "title": "Scaling laws for tiny evals", "summary": "We show that...",
                           "upvotes": 42, "authors": [{"name": "Jane Doe"}]}, "publishedAt": NOW.isoformat()}]
    if "api.github.com/search/repositories" in url:
        return {"items": [{"html_url": "https://github.com/ex/tiny-agent", "full_name": "ex/tiny-agent",
                           "description": "Un agent minimal en 200 lignes", "stargazers_count": 900,
                           "owner": {"login": "ex"}, "created_at": NOW.isoformat(), "topics": ["llm"]}]}
    raise AssertionError(f"URL inattendue : {url}")


LATIN1_FEED = ("<?xml version='1.0' encoding='iso-8859-1'?><rss version='2.0'><channel><title>Blog</title><item>"
               "<title>Un caf\xe9 avec un mod\xe8le</title><link>/posts/1</link></item></channel></rss>").encode("latin-1")


def fake_get_feed(url):
    if url == "https://www.techmeme.com/feed.xml":
        return RSS.encode(), {"Content-Type": "application/rss+xml"}, url
    if url == "https://latin1.ex.com/feed":
        return LATIN1_FEED, {"Content-Type": "application/rss+xml"}, url
    raise RuntimeError("503 Service Unavailable")


def fake_get_text(url):
    if url == "https://blog.ex.com":
        return '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    raise RuntimeError("503 Service Unavailable")


def fake_x_get(self, path, params):
    assert path == "/tweets/search/recent" and "from:alice" in params["query"]
    assert 10 <= params["max_results"] <= 100
    self.reads += 1
    return {"data": [{"id": "77", "text": "Nouveau billet : pourquoi les évals à petite échelle prédisent les grandes.",
                      "author_id": "u1", "created_at": NOW.isoformat(), "public_metrics": {"like_count": 512}}],
            "includes": {"users": [{"id": "u1", "username": "alice", "name": "Alice Martin"}]}}


class FakeClaude:
    """Answers each forced tool call like Claude would, and records the prompts."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, system, content, tool_name, tool_description, schema, model=None, max_tokens=2000):
        self.calls.append({"tool": tool_name, "system": system, "content": content, "model": model})
        ids = [line[1:13] for line in content.splitlines() if line.startswith("[") and line[13:14] == "]"]
        if tool_name == "save_profile":
            return {"summary": "Ingénieur ML qui suit les évals et les agents.", "level": "expert",
                    "topics": [{"name": "évaluation des LLM", "weight": 3}, {"name": "agents", "weight": 2}],
                    "avoid": ["crypto"],
                    "people": [{"name": "Bob Chen", "x_handle": "bobchen", "why": "Écrit sur les évals."},
                               {"name": "Handle invalide", "x_handle": "pas un handle!", "why": "-"}]}
        if tool_name == "pick_items":
            sections = ["essentiel", "voix", "recherche", "ingenierie", "industrie"]
            return {"picks": [{"id": i, "section": sections[n % len(sections)]} for n, i in enumerate(ids)]
                    + [{"id": "inconnu00000", "section": "essentiel"}]}
        if tool_name == "write_digest":
            return {"headline": "Un modèle ouvert géant et des évals qui passent à l'échelle.",
                    "entries": [{"id": i, "title": f"Titre {i}", "summary": f"Résumé {i}.", "why": "Pour tes évals."}
                                for i in ids] + [{"id": "inventé0000x", "title": "X", "summary": "Y"}]}
        if tool_name in ("write_weekly", "propose_projects"):
            project = lambda t: {"title": t, "pitch": "Construis un mini-benchmark.", "why_now": "Le papier de la semaine.",
                                 "refs": ids[:1] + ["inexistant0"], "learn": "Concevoir une éval.",
                                 "plan": ["Lire le papier (1 h)", "Coder l'éval (3 h)", "Tracer la figure (1 h)"],
                                 "deliverable": "Un dépôt et une figure.", "effort": "≈ 5 h", "difficulty": 2,
                                 "kind": "benchmark"}
            if tool_name == "propose_projects":
                return {"projects": [project("Mini-benchmark de calibration"), project("Reproduire la loi d'échelle")]}
            return {"headline": "La semaine des évals.",
                    "trends": [{"text": "Les évals à petite échelle montent.", "refs": ids[:2]}],
                    "entries": [{"id": i, "section": "recherche", "title": f"Semaine {i}", "summary": "Résumé."}
                                for i in ids[:3]],
                    "projects": [project("Reproduire la loi d'échelle"), project("Agent de tri de papiers")]}
        raise AssertionError(tool_name)


@pytest.fixture
def digest_env(clean_db, fake_llm, monkeypatch):
    from app import db

    db.execute("truncate watch, digests, digest_feedback restart identity cascade")
    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    monkeypatch.setattr(sources, "_get_text", fake_get_text)
    monkeypatch.setattr(sources, "_get_feed", fake_get_feed)
    monkeypatch.setattr(twitter.XClient, "_get", fake_x_get)
    claude = FakeClaude()
    monkeypatch.setattr(llm, "call_tool", claude)
    monkeypatch.setattr(agent, "spawn", lambda fn, *args: fn(*args))     # background work runs inline in tests
    s = get_settings()
    monkeypatch.setattr(s, "digest_enabled", True)
    db.execute("""insert into watch (kind, name, x_handle, origin) values ('person', 'Alice Martin', 'alice', 'manual')""")
    return claude


@pytest.fixture
def client(digest_env):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_collect_sources(digest_env):
    from app import db

    cands = sources.collect(NOW - timedelta(hours=26))
    by_source = {}
    for c in cands:
        by_source.setdefault(c.source, []).append(c)
    assert {"Hacker News", "Hugging Face Papers", "GitHub", "X"} <= set(by_source)
    assert len(by_source["GitHub"]) == 1                                    # même dépôt sur 3 sujets : dédoublonné
    assert len({c.key for c in cands}) == len(cands)
    # la même histoire (HN puis Techmeme, URL avec utm_) n'apparaît qu'une fois ; la vieille nouvelle est écartée
    assert sum(1 for c in cands if "open-1t" in c.url) == 1 and "Techmeme" not in by_source
    assert sources.feed("Techmeme", "https://www.techmeme.com/feed.xml", NOW - timedelta(days=1))[0].title \
        == "Un labo publie un modèle ouvert de 1T paramètres"
    latin = sources.feed("Blog", "https://latin1.ex.com/feed", NOW - timedelta(days=1))[0]
    assert latin.title == "Un café avec un modèle" and latin.url == "https://latin1.ex.com/posts/1"
    post = by_source["X"][0]
    assert post.person == "Alice Martin" and post.url == "https://x.com/alice/status/77" and post.kind == "post"
    # flux par défaut ajoutés une fois ; ceux qui échouent sont signalés
    feeds = db.fetchall("select name, last_error, last_ok_at from watch where kind = 'feed'")
    assert len(feeds) == len(sources.DEFAULT_FEEDS)
    assert next(f for f in feeds if f["name"] == "Techmeme")["last_ok_at"]
    assert next(f for f in feeds if f["name"] == "OpenAI News")["last_error"]
    sources.ensure_default_feeds()
    assert db.fetchone("select count(*) n from watch where kind = 'feed'")["n"] == len(sources.DEFAULT_FEEDS)


def test_profile_learns_from_the_kb(digest_env):
    from app import db

    for i in range(2):
        db.execute("""insert into items (kind, status, author, title, tags, source_url)
                      values ('tweet', 'ready', 'Carol Diaz (@caroldiaz)', %s, '{evals}', %s)""",
                   (f"Tweet {i}", f"https://x.com/caroldiaz/status/{i}"))
    db.execute("""insert into items (kind, status, space, category, title, summary, input_text)
                  values ('note', 'ready', 'perso', 'objectif', 'Rejoindre un labo de pointe', 'Travailler sur les modèles.', 'x')""")
    profile.set_manual_text("J'aime les évals et les papiers de post-training.")
    prof = profile.compute(force=True)
    assert prof["topics"][0]["name"] == "évaluation des LLM" and prof["computed_at"]
    prompt = digest_env.calls[-1]["content"]
    assert "J'aime les évals" in prompt and "Rejoindre un labo de pointe" in prompt and "evals" in prompt
    people = {w["x_handle"]: w for w in db.fetchall("select * from watch where kind = 'person'")}
    assert people["caroldiaz"]["origin"] == "auto" and people["caroldiaz"]["status"] == "active"   # 2 tweets sauvés
    assert people["bobchen"]["status"] == "suggested"
    assert len(people) == 3                                                # le handle invalide est ignoré
    assert profile.compute() == prof                                       # encore frais : pas de nouvel appel
    assert "Sujets : évaluation des LLM ★★" in profile.as_text(prof)


def test_daily_digest_end_to_end(client, digest_env, monkeypatch):
    from app import db

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent.append({"host": host})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent[-1]["login"] = user

        def send_message(self, msg):
            sent[-1]["msg"] = msg

    monkeypatch.setattr(render.smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    for k, v in {"smtp_host": "smtp.example.com", "smtp_user": "me@example.com", "smtp_password": "pw",
                 "digest_email_to": "me@example.com"}.items():
        monkeypatch.setattr(s, k, v)
    client.post("/api/ingest", json={"url": "https://gpu.ex.com/profiler"}, headers=AUTH)   # déjà dans la KB

    r = client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH)
    assert r.status_code == 200, r.text
    d = client.get("/api/digests/latest", headers=AUTH).json()
    assert d["status"] == "ready" and d["title"].startswith("Digest du ")
    assert d["headline"].startswith("Un modèle ouvert géant")
    entries = d["data"]["entries"]
    assert entries and all(e["url"].startswith("https://") for e in entries)
    assert [e["section"] for e in entries] == sorted((e["section"] for e in entries), key=render.SECTION_IDS.index)
    assert not any(e["title"] == "X" for e in entries)                     # id inventé par le modèle : ignoré
    assert next(e for e in entries if e["url"] == "https://gpu.ex.com/profiler")["in_kb"] is True
    voice = next(e for e in entries if e["source"] == "X")
    assert voice["person"] == "Alice Martin"
    assert "## L'essentiel" in d["content"] and "](https://" in d["content"]
    pick = next(c for c in digest_env.calls if c["tool"] == "pick_items")
    assert "suivi : Alice Martin" in pick["content"] and "Ingénieur ML" in pick["system"]
    assert next(c for c in digest_env.calls if c["tool"] == "write_digest")["model"] == s.digest_model
    # e-mail : texte + HTML
    msg = sent[-1]["msg"]
    assert msg["To"] == "me@example.com" and "Digest du" in msg["Subject"]
    html = msg.get_body(("html",)).get_content()
    assert "<h2>L&#x27;essentiel</h2>" in html or "<h2>L'essentiel</h2>" in html
    assert '<a href="https://' in html

    # régénérer le même jour garde ses propres histoires ; la liste et le détail sont servis
    client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH)
    again = client.get(f"/api/digests/{d['id']}", headers=AUTH).json()
    assert len(again["data"]["entries"]) == len(entries)
    listing = client.get("/api/digests", headers=AUTH).json()
    assert listing[0]["n_entries"] == len(entries) and listing[0]["kind"] == "daily"
    assert db.fetchone("select attempts from digests where id = %s", (d["id"],))["attempts"] == 1   # reconstruction manuelle

    # retours : garder (→ KB), aimer, refuser, annuler
    key = next(e for e in entries if not e["in_kb"])["key"]
    r = client.post(f"/api/digests/{d['id']}/feedback", json={"target": "entry", "key": key, "vote": 2}, headers=AUTH)
    item_id = r.json()["item_id"]
    assert item_id and db.fetchone("select user_note from items where id = %s", (item_id,))["user_note"].startswith("Repéré dans ton digest")
    other = entries[-1]["key"]
    client.post(f"/api/digests/{d['id']}/feedback", json={"target": "entry", "key": other, "vote": -1}, headers=AUTH)
    fb = {f["entry_key"]: f["vote"] for f in client.get(f"/api/digests/{d['id']}", headers=AUTH).json()["feedback"]}
    assert fb == {key: 2, other: -1}
    client.post(f"/api/digests/{d['id']}/feedback", json={"target": "entry", "key": other, "vote": 0}, headers=AUTH)
    fb = {f["entry_key"]: f["vote"] for f in client.get(f"/api/digests/{d['id']}", headers=AUTH).json()["feedback"]}
    assert fb[other] == 0
    assert client.post(f"/api/digests/{d['id']}/feedback", json={"target": "entry", "key": "nope", "vote": 1},
                       headers=AUTH).status_code == 404

    # régénérer ce digest-là (et pas un autre) ; refusé s'il est déjà en cours
    assert client.post(f"/api/digests/{d['id']}/regenerate", headers=AUTH).json() == {"ok": True, "id": d["id"]}
    db.execute("update digests set status = 'generating', updated_at = now() where id = %s", (d["id"],))
    assert client.post(f"/api/digests/{d['id']}/regenerate", headers=AUTH).status_code == 409
    assert client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH).json()["id"] == d["id"]
    assert db.fetchone("select status from digests where id = %s", (d["id"],))["status"] == "generating"   # pas relancé
    db.execute("update digests set status = 'ready' where id = %s", (d["id"],))

    # le lendemain, les histoires déjà servies ne reviennent pas
    db.execute("update digests set period_start = period_start - 1, period_end = period_end - 1")
    client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH)
    today = client.get("/api/digests/latest", headers=AUTH).json()
    assert today["id"] != d["id"] and not today["data"]["entries"]
    assert today["headline"].startswith("Rien de neuf")


def test_weekly_digest_projects_and_scheduler(client, digest_env):
    from app import db

    paris = ZoneInfo("Europe/Paris")
    monday = datetime(2026, 10, 5, 7, 5, tzinfo=paris)
    assert agent.run_due(monday.replace(hour=6)) == []                     # trop tôt
    # un digest de la veille sert de matière à la semaine
    sunday = date(2026, 10, 4)
    db.execute(
        """insert into digests (kind, period_start, period_end, status, headline, data)
           values ('daily', %s, %s, 'ready', 'Dimanche', %s)""",
        (sunday, sunday, db.jsonb({"entries": [{
            "key": "dimanche0001", "section": "recherche", "title": "Un papier du dimanche", "summary": "Résumé.",
            "url": "https://papers.ex.com/dimanche", "source": "Hugging Face Papers", "kind": "paper"}]})))
    done = agent.run_due(monday)
    assert len(done) == 2
    weekly = db.fetchone("select * from digests where kind = 'weekly'")
    assert weekly["period_start"] == date(2026, 9, 28) and weekly["period_end"] == sunday and weekly["status"] == "ready"
    data = weekly["data"]
    assert [p["title"] for p in data["projects"]] == ["Reproduire la loi d'échelle", "Agent de tri de papiers"]
    assert all(p["key"] and p["refs"] and "inexistant0" not in p["refs"] for p in data["projects"])
    assert data["trends"][0]["refs"]
    visible = [e for e in data["entries"] if not e.get("hidden")]
    assert len(visible) == 3 and all(e["section"] == "recherche" for e in visible)
    assert "## La semaine en bref" in weekly["content"] and "## Projets pour cette semaine" in weekly["content"]
    call = next(c for c in digest_env.calls if c["tool"] == "write_weekly")
    assert "Un papier du dimanche" in call["content"] and call["model"] == get_settings().digest_weekly_model
    assert "Projets déjà proposés" in call["content"]
    assert agent.run_due(monday + timedelta(minutes=30)) == []             # rien à refaire

    # « je le fais » : le projet devient une note de la KB
    key = data["projects"][0]["key"]
    r = client.post(f"/api/digests/{weekly['id']}/feedback", json={"target": "project", "key": key, "vote": 2},
                    headers=AUTH)
    # changer d'avis puis recliquer ne crée pas une deuxième note
    for v in (1, 0, 2):
        again = client.post(f"/api/digests/{weekly['id']}/feedback", json={"target": "project", "key": key, "vote": v},
                            headers=AUTH)
    assert again.json()["item_id"] == r.json()["item_id"]
    assert db.fetchone("select count(*) n from items where 'projet' = any(tags)")["n"] == 1
    note = db.fetchone("select kind, title, tags, input_text from items where id = %s", (r.json()["item_id"],))
    assert note["kind"] == "note" and note["title"] == "Reproduire la loi d'échelle" and "projet" in note["tags"]
    assert "**Plan**" in note["input_text"] and "Proposé par le digest" in note["input_text"]
    drain()

    # d'autres projets à la demande, sans doublon
    assert client.post(f"/api/digests/{weekly['id']}/projects", headers=AUTH).json()["ok"]
    after = client.get(f"/api/digests/{weekly['id']}", headers=AUTH).json()
    titles = [p["title"] for p in after["data"]["projects"]]
    assert titles == ["Reproduire la loi d'échelle", "Agent de tri de papiers", "Mini-benchmark de calibration"]
    assert "projects_pending" not in after["data"]
    db.execute("""update digests set data = data || '{"projects_pending": true}' where id = %s""", (weekly["id"],))
    assert client.post(f"/api/digests/{weekly['id']}/projects", headers=AUTH).status_code == 409
    assert client.post(f"/api/digests/{weekly['id']}/regenerate", headers=AUTH).status_code == 409
    assert "Reproduire la loi d'échelle (il l'a fait)" in digest_env.calls[-1]["content"]


def test_failed_digest_is_retried(digest_env, monkeypatch):
    from app import db

    # toutes les sources en panne : une erreur (réessayée), pas un digest vide
    monkeypatch.setattr(sources, "_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("réseau")))
    monkeypatch.setattr(sources, "_get_feed", lambda url: (_ for _ in ()).throw(RuntimeError("réseau")))
    monkeypatch.setattr(twitter.XClient, "_get", lambda self, path, params: (_ for _ in ()).throw(RuntimeError("réseau")))
    tuesday = datetime(2026, 10, 6, 8, 0, tzinfo=ZoneInfo("Europe/Paris"))
    ids = agent.run_due(tuesday)                     # la semaine manquante est rattrapée un mardi, puis le jour
    assert len(ids) == 2
    weekly = db.fetchone("select * from digests where kind = 'weekly'")
    assert (weekly["period_start"], weekly["period_end"]) == (date(2026, 9, 28), date(2026, 10, 4))
    daily = db.fetchone("select * from digests where kind = 'daily'")
    assert daily["status"] == "error" and "Aucune source" in daily["error"]
    assert agent.run_due(tuesday) == []                                    # pas avant une heure
    db.execute("update digests set updated_at = now() - interval '2 hours'")
    monkeypatch.setattr(agent, "build_daily", lambda day: {"headline": "OK", "data": {"entries": []}, "model": None})
    monkeypatch.setattr(agent, "build_weekly", lambda a, b: {"headline": "OK", "data": {"entries": []}, "model": None})
    assert sorted(agent.run_due(tuesday)) == sorted(ids)
    assert {r["status"] for r in db.fetchall("select status from digests")} == {"ready"}
    assert db.fetchone("select attempts from digests where kind = 'daily'")["attempts"] == 2


def test_tolerates_model_quirks(digest_env, monkeypatch):
    from app import db

    assert agent.section_id("Ingénierie") == "ingenierie" and agent.section_id("Modèles et labs") == "modeles"
    assert agent.section_id("n'importe quoi") is None
    real = digest_env.__call__

    def quirky(**kw):
        out = real(**kw)
        if kw["tool_name"] == "pick_items":
            out["picks"][0]["section"] = "ingénierie"
            out["picks"][1].pop("section")
        if kw["tool_name"] == "save_profile":
            out["topics"] = [{"name": "évals", "weight": "3"}, {"name": "agents", "weight": None}, {"weight": 2}]
        return out

    monkeypatch.setattr(llm, "call_tool", quirky)
    profile.compute(force=True)
    assert profile.get()["topics"] == [{"name": "évals", "weight": 3}, {"name": "agents", "weight": 1}]
    agent.run_due(datetime(2026, 10, 6, 9, 0, tzinfo=ZoneInfo("Europe/Paris")))
    daily = db.fetchone("select status, data from digests where kind = 'daily'")
    assert daily["status"] == "ready" and any(e["section"] == "ingenierie" for e in daily["data"]["entries"])


def test_interests_and_watch_api(client, digest_env):
    r = client.get("/api/interests", headers=AUTH).json()
    assert r["schedule"]["enabled"] is True and r["people"][0]["x_handle"] == "alice"
    assert client.put("/api/interests", json={"text": "Les évals, le post-training."}, headers=AUTH).json()["ok"]
    assert client.get("/api/interests", headers=AUTH).json()["text"] == "Les évals, le post-training."

    bob = client.post("/api/watch", json={"x_handle": "@bobchen", "name": "Bob Chen"}, headers=AUTH).json()
    assert bob["kind"] == "person" and bob["x_handle"] == "bobchen" and bob["status"] == "active"
    blog = client.post("/api/watch", json={"url": "https://blog.ex.com", "name": "Le blog de Dan"}, headers=AUTH).json()
    assert blog["kind"] == "feed" and blog["feed_url"] == "https://blog.ex.com/feed.xml"
    assert client.post("/api/watch", json={"x_handle": "pas valide!"}, headers=AUTH).status_code == 400
    assert client.post("/api/watch", json={"url": "https://down.ex.com"}, headers=AUTH).status_code == 400
    again = client.post("/api/watch", json={"x_handle": "BobChen"}, headers=AUTH).json()
    assert again["id"] == bob["id"]                                        # pas de doublon
    assert client.patch(f"/api/watch/{bob['id']}", json={"status": "muted"}, headers=AUTH).json()["status"] == "muted"
    assert client.patch(f"/api/watch/{bob['id']}", json={"status": "zzz"}, headers=AUTH).status_code == 400
    assert client.delete(f"/api/watch/{blog['id']}", headers=AUTH).json()["ok"]
    prof = client.post("/api/interests/refresh", headers=AUTH).json()
    assert prof["summary"].startswith("Ingénieur ML")


def test_mcp_digest_tools(client, digest_env):
    client.post("/api/digests/generate", json={"kind": "daily"}, headers=AUTH)
    path = "/mcp/mcp-secret"
    tools = {t["name"] for t in _mcp(client, path, "tools/list").json()["result"]["tools"]}
    assert {"get_digest", "get_interests"} <= tools
    text = _mcp(client, path, "tools/call", {"name": "get_digest", "arguments": {}}).json()["result"]["content"][0]["text"]
    assert text.startswith("# Digest du ") and "## L'essentiel" in text
    text = _mcp(client, path, "tools/call", {"name": "get_digest", "arguments": {"kind": "weekly"}}).json()
    assert "Aucun digest" in text["result"]["content"][0]["text"]
    text = _mcp(client, path, "tools/call", {"name": "get_interests", "arguments": {}}).json()["result"]["content"][0]["text"]
    assert "Alice Martin (@alice)" in text and "Techmeme" in text


def test_markdown_to_html():
    html = render.to_html("# Titre\n*Accroche*\n\n## Section\n- **[Lien](https://ex.com/a?b=1&c=2)** : texte <b>\n"
                          "  1. étape un\n  2. étape deux\n\nParagraphe.")
    assert "<h1>Titre</h1>" in html and "<em>Accroche</em>" in html
    assert '<a href="https://ex.com/a?b=1&amp;c=2">Lien</a>' in html and "&lt;b&gt;" in html
    assert "<ol>" in html and html.count("<li>") == 3 and "<p>Paragraphe.</p>" in html
    assert json.dumps(render.SECTION_IDS) == json.dumps(["essentiel", "industrie", "modeles", "voix", "recherche", "ingenierie"])


def test_try_again_on_models_that_refuse_forced_tools(client, digest_env, monkeypatch):
    """The production case: Sonnet 5.5 refused the forced tool call, the digest showed the raw 400. With the real
    call_tool, « Réessayer » now writes it through structured outputs, and an API error reads like a sentence."""
    from app import db

    schemas = {"pick_items": agent.PICK_SCHEMA, "write_digest": agent.DAILY_SCHEMA, "write_weekly": agent.WEEKLY_SCHEMA,
               "propose_projects": agent.PROJECTS_SCHEMA, "save_profile": profile.PROFILE_SCHEMA}
    seen = []

    class API(FakeClaudeAPI):
        def _call(self, **kw):
            forced = (kw.get("tool_choice") or {}).get("type") == "tool"
            seen.append((kw["model"], "forced" if forced else "structured"))
            if forced and kw["model"] != "claude-haiku-5-5":
                raise _bad_request(REFUSED)
            if forced:
                name = kw["tools"][0]["name"]
            else:
                name = next(n for n, sch in schemas.items() if llm.strict_schema(sch) == kw["output_config"]["format"]["schema"])
            self.answer = digest_env(system=kw["system"], content=kw["messages"][0]["content"], tool_name=name,
                                     tool_description="", schema=None, model=kw["model"])
            return super()._call(**kw)

    monkeypatch.setattr(llm, "call_tool", REAL_CALL_TOOL)
    monkeypatch.setattr(llm, "_NO_FORCED_TOOL", set())
    monkeypatch.setattr(llm, "client", lambda: API(None))

    # the digest as it was in production: in error after the refused call
    row = db.fetchone("""insert into digests (kind, period_start, period_end, status, error)
                         values ('daily', current_date, current_date, 'error', %s) returning id""",
                      ("BadRequestError: Error code: 400 - {...}",))
    assert client.post(f"/api/digests/{row['id']}/regenerate", headers=AUTH).json()["ok"] is True
    d = client.get(f"/api/digests/{row['id']}", headers=AUTH).json()
    assert d["status"] == "ready" and d["error"] is None
    assert d["headline"].startswith("Un modèle ouvert géant")
    assert len(d["data"]["entries"]) >= 3
    assert ("claude-sonnet-5-5", "forced") in seen and ("claude-sonnet-5-5", "structured") in seen
    assert ("claude-haiku-5-5", "forced") in seen and ("claude-haiku-5-5", "structured") not in seen

    # an error from the API is stored as a readable sentence, not as the raw JSON of the answer
    def down(**kw):
        raise _bad_request("Your credit balance is too low to access the Anthropic API.")

    monkeypatch.setattr(llm, "client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(down),
                                                                                        "stream": staticmethod(down)})})())
    assert client.post(f"/api/digests/{row['id']}/regenerate", headers=AUTH).json()["ok"] is True
    d = client.get(f"/api/digests/{row['id']}", headers=AUTH).json()
    assert d["status"] == "error"
    assert d["error"] == "Claude a répondu 400 : Your credit balance is too low to access the Anthropic API."
