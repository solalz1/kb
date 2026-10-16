"""The wiki: pages Claude keeps up to date from the items (Claude faked by conftest._fake_wiki, real Postgres), the
question mode that reads it, the nightly review, and the Obsidian vault (zip, and a two-way sync with a fake GitHub)."""

import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app import extractors, obsidian, wiki
from app.config import get_settings
from app.extractors.base import Extracted

from .conftest import drain
from .test_e2e import AUTH

PAGES = {
    "https://blog.ex.com/agents": Extracted(
        kind="article", title="Construire un agent fiable", source_url="https://blog.ex.com/agents", author="Jane Doe",
        content="Un agent doit être évalué sur de vraies tâches, avec un benchmark qui mesure aussi le coût. " * 8),
    "https://blog.ex.com/agents-2": Extracted(
        kind="article", title="Les agents en production", source_url="https://blog.ex.com/agents-2", author="Bob",
        content="En production, un agent échoue surtout sur la récupération d'erreurs et les outils mal décrits. " * 8),
    "https://blog.ex.com/mort": Extracted(
        kind="article", title="Page vide", source_url="https://blog.ex.com/mort", content="lien mort " * 20),
}


@pytest.fixture
def client(clean_db, fake_llm, monkeypatch):
    def fake_extract_url(url):
        from app import urls

        return PAGES.get(url) or PAGES[urls.classify(url).canonical]

    monkeypatch.setattr(extractors, "extract_url", fake_extract_url)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _add(client, url: str) -> str:
    r = client.post("/api/ingest", json={"url": url}, headers=AUTH)
    assert r.status_code == 200, r.text
    drain()
    return r.json()["id"]


def _pages():
    from app import db

    return {p["title"]: p for p in db.fetchall(f"select {wiki.PAGE_FIELDS} from wiki_pages where not archived")}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_items_are_filed_into_theme_pages_not_one_page_each(client, fake_llm):
    from app import db

    first = _add(client, "https://blog.ex.com/agents")
    # the item pipeline queued it: the wiki works its own queue
    assert db.fetchone("select kind, status from wiki_jobs where item_id = %s", (first,)) == {"kind": "ingest",
                                                                                            "status": "pending"}
    assert wiki.drain() == 1
    pages = _pages()
    assert set(pages) == {"Agents IA", "Évaluation des agents", "Andrej Karpathy"}
    agents = pages["Agents IA"]
    assert agents["section"] == "research" and agents["kind"] == "theme" and agents["revision"] == 1
    assert f"[src:{wiki.ref(first)}]" in agents["content"]
    assert agents["links"] == ["évaluation des agents"]          # [[Évaluation des agents]], lowercased
    assert pages["Andrej Karpathy"]["kind"] == "entity"

    second = _add(client, "https://blog.ex.com/agents-2")
    wiki.drain()
    pages = _pages()
    assert set(pages) == {"Agents IA", "Évaluation des agents", "Andrej Karpathy"}   # two items, still three pages
    agents = pages["Agents IA"]
    assert agents["revision"] == 2
    assert "## Construire un agent fiable" in agents["content"] and "## Les agents en production" in agents["content"]
    assert {wiki.ref(first), wiki.ref(second)} <= set(wiki.cites_of(agents["content"]))
    # Claude was told which pages exist, and asked to update them rather than create new ones
    assert "Agents IA [theme]" in fake_llm["wiki_plan"][-1]["index"]
    assert fake_llm["wiki_write"][-1]["ref"] == wiki.ref(second)

    sources = db.fetchall("select item_id::text from wiki_page_sources where page_id = %s", (agents["id"],))
    assert {r["item_id"] for r in sources} == {first, second}
    history = db.fetchall("select revision, author, reason from wiki_revisions where page_id = %s order by revision",
                          (agents["id"],))
    assert history == [{"revision": 1, "author": "claude", "reason": "ingest"},
                       {"revision": 2, "author": "claude", "reason": "ingest"}]
    entries = wiki.recent_log()
    assert [e["kind"] for e in entries] == ["ingest", "ingest"]
    assert entries[0]["title"] == "Les agents en production"
    assert entries[1]["detail"]["created"] and not entries[0]["detail"]["created"]
    assert "## [" in wiki.log_markdown() and "ingest | Construire un agent fiable" in wiki.log_markdown()


def test_a_source_with_nothing_durable_touches_no_page(client):
    _add(client, "https://blog.ex.com/mort")
    wiki.drain()
    assert _pages() == {}
    assert wiki.recent_log()[0]["detail"]["nothing"] is True


def test_a_page_changed_meanwhile_makes_the_job_run_again(client, fake_llm, monkeypatch):
    from app import db, llm

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    _add(client, "https://blog.ex.com/agents-2")
    write = llm.wiki_write

    def write_while_someone_edits(**kw):
        # an edit lands (from Obsidian, say) while Claude writes: his version is now stale
        db.execute("update wiki_pages set revision = revision + 1 where title = 'Agents IA'")
        monkeypatch.setattr(llm, "wiki_write", write)
        return write(**kw)

    monkeypatch.setattr(llm, "wiki_write", write_while_someone_edits)
    assert wiki.run_once()
    job = db.fetchone("select status, error, attempts from wiki_jobs where kind = 'ingest' order by id desc limit 1")
    assert job["status"] == "pending" and "a changé" in job["error"]
    # nothing of that run was kept: the pages were written in one transaction
    assert _pages()["Andrej Karpathy"]["revision"] == 1
    db.execute("update wiki_jobs set next_attempt_at = null")
    assert wiki.drain() == 1
    assert _pages()["Andrej Karpathy"]["revision"] == 2 and _pages()["Agents IA"]["revision"] == 3


def test_reprocessed_item_joins_the_queue_again_once(client):
    from app import db

    item = _add(client, "https://blog.ex.com/agents")
    wiki.enqueue_item(item)          # already waiting: not twice
    assert db.fetchone("select count(*)::int n from wiki_jobs")["n"] == 1
    wiki.drain()
    assert client.post(f"/api/items/{item}/reprocess", headers=AUTH).status_code == 200
    drain()
    assert db.fetchone("select count(*)::int n from wiki_jobs where status = 'pending'")["n"] == 1


def test_items_of_before_join_the_queue_once(clean_db, monkeypatch):
    from app import db, migrate

    db.execute("insert into items (kind, title, status) values ('note', 'Avant le wiki', 'ready'), "
               "('note', 'En erreur', 'error')")
    migrate.run()
    assert db.fetchone("select count(*)::int n from wiki_jobs")["n"] == 0     # the schema queues nothing
    monkeypatch.setattr(get_settings(), "wiki_enabled", False)
    assert wiki.backfill_once() == 0                                          # the wiki off: nothing either
    monkeypatch.setattr(get_settings(), "wiki_enabled", True)
    assert wiki.backfill_once() == 1
    db.execute("update wiki_jobs set status = 'done'")
    db.execute("delete from wiki_jobs")                                       # even after old jobs are purged
    assert wiki.backfill_once() == 0
    assert db.fetchone("select count(*)::int n from wiki_jobs")["n"] == 0


def test_titles_and_links():
    assert wiki.clean_title('  Agents: le "vrai" coût / tâche?  ') == "Agents le vrai coût tâche"
    assert wiki.clean_title("") == "Sans titre"
    assert wiki.slugify("Évaluation des LLM") == "evaluation-des-llm"
    assert wiki.links_of("Voir [[Agents IA]], [[Évaluation#Coût|le coût]] et [[agents ia]] ; [[Sources/x (12345678)|↗]]") \
        == ["agents ia", "évaluation"]
    assert wiki.cites_of("a [src:ABCDEF12] b [src:abcdef12] [src:zz]") == ["abcdef12"]
    assert wiki.section_from_label("Lectures") == "reading" and wiki.section_from_label("Competitive analysis") == "competitive"
    assert wiki.section_from_label("nope") is None


# ---------------------------------------------------------------------------
# Asking the wiki
# ---------------------------------------------------------------------------

def _events(r) -> list[dict]:
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_question_answered_from_the_wiki(client, fake_llm):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "Que sais-je des agents IA ?"}],
                                       "source": "wiki"}, headers=AUTH)
    events = _events(r)
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources[0]["kind"] == "wiki" and sources[0]["title"] == "Agents IA"
    assert sources[0]["kb_url"] == "https://kb.example.com/wiki/agents-ia"
    assert events[-1]["type"] == "done"
    sent = fake_llm["stream"][-1]
    assert "Mode Wiki" in sent["system"]
    # the page's references are named for Claude, not left as codes
    assert "Page du wiki : Agents IA" in sent["messages"][-1]["content"]
    assert "(source : Construire un agent fiable)" in sent["messages"][-1]["content"]
    # RAG stays one switch away
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "agents"}], "source": "rag"}, headers=AUTH)
    assert next(e for e in _events(r) if e["type"] == "sources")["sources"][0]["kind"] == "article"
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "source": "web"},
                       headers=AUTH).status_code == 400


def test_empty_wiki_falls_back_to_the_items(client):
    _add(client, "https://blog.ex.com/agents")      # not integrated yet
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "agents ?"}], "source": "wiki"},
                    headers=AUTH)
    events = _events(r)
    assert {"type": "fallback", "source": "rag"} in events
    assert next(e for e in events if e["type"] == "sources")["sources"][0]["kind"] == "article"


def test_an_answer_worth_keeping_becomes_a_page(client, fake_llm):
    item = _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    agents = _pages()["Agents IA"]
    r = client.post("/api/wiki/answers", json={
        "question": "Comment évaluer un agent ?", "answer": "Sur de vraies tâches [1], en mesurant le coût [2].",
        "sources": [{"n": 1, "id": agents["id"], "title": "Agents IA", "kind": "wiki"},
                    {"n": 2, "id": item, "title": "Construire un agent fiable", "kind": "article"}]}, headers=AUTH)
    job = r.json()["job_id"]
    assert client.get(f"/api/wiki/jobs/{job}", headers=AUTH).json()["status"] == "pending"
    wiki.drain()
    done = client.get(f"/api/wiki/jobs/{job}", headers=AUTH).json()
    assert done["status"] == "done" and done["result"]["slug"] == "reponse-comment-evaluer-un-agent"
    page = _pages()["Réponse Comment évaluer un agent"]
    assert page["kind"] == "answer"
    assert page["content"] == ("*Question : Comment évaluer un agent ?*\n\n"
                               f"Sur de vraies tâches [[Agents IA]], en mesurant le coût [src:{wiki.ref(item)}].")
    assert page["title"] in [b["title"] for b in wiki.backlinks("Agents IA")]
    assert wiki.recent_log()[0]["kind"] == "answer"
    assert client.post("/api/wiki/answers", json={"answer": " "}, headers=AUTH).status_code == 400


# ---------------------------------------------------------------------------
# The nightly review
# ---------------------------------------------------------------------------

def test_nightly_review_fixes_merges_and_suggests(client, fake_llm):
    from app import db

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    dup = wiki.write_from_chat(title="Agents IA (doublon)", content="Même sujet. Voir [[Page inexistante]].")
    # another page linked to the duplicate: it is pointed at the page that stays
    wiki.write_from_chat(title="Andrej Karpathy", content="Parle des [[Agents IA (doublon)]] [src:00000000].")
    found = wiki.findings(wiki.all_pages(with_content=True))
    assert {"page": "Agents IA (doublon)", "missing": "page inexistante"} in found["broken_links"]
    assert "Évaluation des agents" not in found["orphans"]

    job = client.post("/api/wiki/lint", headers=AUTH).json()["job_id"]
    wiki.drain()
    result = client.get(f"/api/wiki/jobs/{job}", headers=AUTH).json()["result"]
    assert result["merged"] == [{"from": "Agents IA (doublon)", "into": "Agents IA", "reason": "Même sujet"}]
    assert result["fixed"] and result["suggestions"][0]["kind"] == "question"
    assert "Liens vers des pages inexistantes" in fake_llm["wiki_lint"][0]["findings"]
    pages = _pages()
    assert "Agents IA (doublon)" not in pages
    assert "[[Agents IA]]" in pages["Andrej Karpathy"]["content"]
    archived = db.fetchone("select archived, metadata from wiki_pages where id = %s", (dup["id"],))
    assert archived["archived"] and archived["metadata"]["merged_into"] == pages["Agents IA"]["id"]
    assert wiki.suggestions()["items"][0]["text"].startswith("Creuser")
    last = wiki.last_lint()
    assert last["title"].startswith("Relecture : ") and last["detail"]["merged"]
    # an archived page still opens, and says where it went
    detail = client.get(f"/api/wiki/pages/{dup['id']}", headers=AUTH).json()
    assert detail["merged_into"]["title"] == "Agents IA"


def test_review_runs_once_a_night_and_only_after_changes(client):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    tz = ZoneInfo(get_settings().digest_timezone)
    assert wiki.lint_due(datetime(2026, 10, 16, 2, 0, tzinfo=tz)) is None       # before WIKI_LINT_HOUR
    job = wiki.lint_due(datetime(2026, 10, 16, 3, 5, tzinfo=tz))
    assert job is not None
    assert wiki.lint_due(datetime(2026, 10, 16, 4, 0, tzinfo=tz)) is None       # once a night
    wiki.drain()
    assert wiki.lint_due(datetime(2026, 10, 17, 3, 5, tzinfo=tz)) is None       # nothing new since: no review


# ---------------------------------------------------------------------------
# API and connector
# ---------------------------------------------------------------------------

def test_wiki_api(client):
    assert client.get("/api/wiki").status_code == 401
    item = _add(client, "https://blog.ex.com/agents")
    over = client.get("/api/wiki", headers=AUTH).json()
    assert over["queue"]["pending"] == 1 and over["pages"] == []
    wiki.drain()
    over = client.get("/api/wiki", headers=AUTH).json()
    assert {s["key"]: s["count"] for s in over["sections"]} == {"personal": 0, "research": 3, "reading": 0,
                                                                "business": 0, "competitive": 0}
    assert over["obsidian"] == {"configured": False, "missing": ["WIKI_GIT_REPO", "WIKI_GIT_TOKEN"]}
    page = client.get("/api/wiki/pages/agents-ia", headers=AUTH).json()
    assert page["title"] == "Agents IA" and page["sources"][0]["id"] == item
    assert page["refs"][wiki.ref(item)]["title"] == "Construire un agent fiable"
    assert page["links_resolved"] == {"évaluation des agents": "evaluation-des-agents"}
    assert page["vault_path"] == "Wiki/Recherche/Agents IA"
    assert [b["title"] for b in client.get("/api/wiki/pages/evaluation-des-agents", headers=AUTH).json()["backlinks"]] \
        == ["Agents IA"]
    assert page["history"][0]["reason"] == "ingest"
    assert client.get("/api/wiki/pages/Agents IA", headers=AUTH).json()["slug"] == "agents-ia"   # by title too
    assert client.get("/api/wiki/pages/nope", headers=AUTH).status_code == 404
    assert client.get("/api/wiki/log", headers=AUTH).json()[0]["kind"] == "ingest"
    assert client.post("/api/wiki/sync", headers=AUTH).status_code == 400


def test_user_conventions_go_to_claude(client, fake_llm):
    r = client.put("/api/wiki/schema", json={"text": "Une page par livre, avec ses citations."}, headers=AUTH)
    assert r.json()["text"] == "Une page par livre, avec ses citations."
    assert client.get("/api/wiki/schema", headers=AUTH).json()["text"].startswith("Une page par livre")
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    assert "Conventions de l'utilisateur (prioritaires) :\nUne page par livre" in fake_llm["wiki_plan"][-1]["schema"]


def test_connector_reads_and_writes_the_wiki(client):
    from .test_e2e import _mcp

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()

    def call(name, args, rid):
        r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": args}, rid=rid)
        assert r.status_code == 200 and not r.json()["result"].get("isError"), r.text
        return r.json()["result"]["content"][0]["text"]

    index = call("wiki_index", {}, 1)
    assert "## Recherche (research)" in index and "- Agents IA [theme]" in index
    page = call("read_wiki_page", {"title": "agents ia"}, 2)
    assert page.startswith("# Agents IA") and "https://kb.example.com/wiki/agents-ia" in page
    assert "## Sources" in page and "Construire un agent fiable" in page
    assert "Agents IA" in call("search_wiki", {"query": "agent"}, 3)
    saved = call("write_wiki_page", {"title": "Coûts des agents", "content": "Le coût compte. [[Agents IA]]",
                                     "section": "Recherche"}, 4)
    assert saved.startswith("Page enregistrée : Coûts des agents (révision 1)")
    assert _pages()["Coûts des agents"]["section"] == "research"
    assert "Section inconnue" in call("write_wiki_page", {"title": "X", "content": "y", "section": "cuisine"}, 5)
    assert "Page introuvable" in call("read_wiki_page", {"title": "Rien du tout"}, 6)


def test_weekly_projects_build_on_the_wiki(client):
    from datetime import date

    from app.digest import agent

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    client.post("/api/wiki/lint", headers=AUTH)
    wiki.drain()
    context = agent._project_context(date(2026, 1, 1))
    assert "- Agents IA : " in context and "Creuser l'évaluation des agents" in context


# ---------------------------------------------------------------------------
# Obsidian
# ---------------------------------------------------------------------------

def test_vault_zip(client):
    item = _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    r = client.get("/api/wiki/export", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    source = f"KB-Wiki/Sources/Construire un agent fiable ({wiki.ref(item)}).md"
    assert {"KB-Wiki/index.md", "KB-Wiki/log.md", "KB-Wiki/SCHEMA.md", "KB-Wiki/.obsidian/graph.json",
            "KB-Wiki/Wiki/Recherche/Agents IA.md", source} <= names
    note = z.read("KB-Wiki/Wiki/Recherche/Agents IA.md").decode()
    page = _pages()["Agents IA"]
    assert note.startswith(f'---\nkb_id: "{page["id"]}"\nsection: "Recherche"\ntype: "thème"')
    assert f"Un fait [[Sources/Construire un agent fiable ({wiki.ref(item)})|↗]]." in note
    assert "Voir aussi [[Évaluation des agents]]." in note
    assert note.split(obsidian.MARKER)[1].count("[[Sources/") == 1
    assert "[[Agents IA]]" in z.read(source).decode()
    assert "- [[Agents IA]] — Ce que le wiki sait de Agents IA · 1 source" in z.read("KB-Wiki/index.md").decode()
    graph = json.loads(z.read("KB-Wiki/.obsidian/graph.json"))
    assert {'path:"Wiki/Recherche"', 'path:"Sources"'} <= {g["query"] for g in graph["colorGroups"]}
    # read back, a note gives the page's content exactly
    meta, extra, body = obsidian.parse_note(note)
    assert meta["kb_id"] == page["id"] and extra == "" and body == page["content"]


def _sha(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class FakeGitHub:
    """A repository in memory, behind the few routes of the Git Data API the sync uses."""

    def __init__(self):
        self.blobs: dict[str, str] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.commits: dict[str, dict] = {}
        self.ref: str | None = None          # None: an empty repository
        self.calls: list[tuple[str, str]] = []
        self.race = False                    # the next ref update finds that someone pushed meanwhile

    def _tree(self, files: dict[str, str]) -> str:
        sha = hashlib.sha1(json.dumps(sorted(files.items())).encode()).hexdigest()
        self.trees[sha] = dict(files)
        return sha

    def _commit(self, files: dict[str, str], parents: list[str], message: str) -> str:
        sha = hashlib.sha1(f"{message}{parents}{sorted(files.items())}{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = {"tree": self._tree(files), "parents": parents, "message": message}
        return sha

    def files(self) -> dict[str, str]:
        return {p: self.blobs[s] for p, s in self.trees[self.commits[self.ref]["tree"]].items()}

    def user_commit(self, changes: dict[str, str | None]) -> None:
        """What the Obsidian Git plugin pushes: notes changed, added or deleted on the Mac."""
        tree = dict(self.trees[self.commits[self.ref]["tree"]])
        for path, text in changes.items():
            if text is None:
                tree.pop(path, None)
            else:
                self.blobs[_sha(text)] = text
                tree[path] = _sha(text)
        self.ref = self._commit(tree, [self.ref], "vault backup")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ghp_test"
        path = request.url.path.removeprefix("/repos/me/kb-wiki")
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path))
        if request.method == "GET" and path == "/git/ref/heads/main":
            if self.ref is None:
                return httpx.Response(409, json={"message": "Git Repository is empty."})
            return httpx.Response(200, json={"object": {"sha": self.ref}})
        if request.method == "PUT" and path == "/contents/README.md":
            text = base64.b64decode(body["content"]).decode()
            self.blobs[_sha(text)] = text
            self.ref = self._commit({"README.md": _sha(text)}, [], body["message"])
            return httpx.Response(201, json={"commit": {"sha": self.ref}})
        if request.method == "GET" and path.startswith("/git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"sha": sha, "tree": {"sha": self.commits[sha]["tree"]}})
        if request.method == "GET" and path.startswith("/git/trees/"):
            files = self.trees[path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"tree": [{"path": p, "type": "blob", "sha": s, "mode": "100644"}
                                                      for p, s in files.items()], "truncated": False})
        if request.method == "GET" and path.startswith("/git/blobs/"):
            text = self.blobs[path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})
        if request.method == "POST" and path == "/git/trees":
            files = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if e.get("sha", "") is None:
                    files.pop(e["path"], None)
                else:
                    self.blobs[_sha(e["content"])] = e["content"]
                    files[e["path"]] = _sha(e["content"])
            return httpx.Response(201, json={"sha": self._tree(files)})
        if request.method == "POST" and path == "/git/commits":
            sha = hashlib.sha1(f"{body}{len(self.commits)}".encode()).hexdigest()
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
            return httpx.Response(201, json={"sha": sha})
        if request.method == "PATCH" and path == "/git/refs/heads/main":
            if self.race or self.commits[body["sha"]]["parents"] != [self.ref]:
                self.race = False
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            self.ref = body["sha"]
            return httpx.Response(200, json={"object": {"sha": self.ref}})
        return httpx.Response(404, json={"message": f"no route {request.method} {path}"})

    def commits_made(self) -> int:
        return sum(1 for m, p in self.calls if m == "PATCH")


@pytest.fixture
def github(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "wiki_git_repo", "me/kb-wiki")
    monkeypatch.setattr(s, "wiki_git_token", "ghp_test")
    fake = FakeGitHub()
    monkeypatch.setattr(obsidian, "_client", httpx.Client(base_url=obsidian.API, transport=httpx.MockTransport(fake)))
    return fake


def test_repo_url_is_accepted_for_the_setting():
    from app.config import Settings

    assert Settings(wiki_git_repo="https://github.com/me/kb-wiki.git").wiki_git_repo == "me/kb-wiki"
    assert Settings(wiki_git_repo="git@github.com:me/kb-wiki").wiki_git_repo == "me/kb-wiki"


def test_obsidian_sync_both_ways(client, github, fake_llm):
    from app import db

    item = _add(client, "https://blog.ex.com/agents")
    wiki.drain()

    # first pass on an empty repository: a README to start the branch, then the whole vault in one commit
    res = obsidian.sync_once()
    assert res["commit"] and res["imported"] == []
    files = github.files()
    assert {"README.md", "index.md", "log.md", "SCHEMA.md", ".gitignore", ".obsidian/graph.json",
            "Wiki/Recherche/Agents IA.md", f"Sources/Construire un agent fiable ({wiki.ref(item)}).md"} <= set(files)
    assert obsidian.status()["last_sync_at"] and obsidian.status()["url"] == "https://github.com/me/kb-wiki"
    # nothing changed: nothing written, and only the branch's head was asked for
    before = len(github.calls)
    assert obsidian.sync_once()["commit"] is None and github.commits_made() == 1
    assert github.calls[before:] == [("GET", "/git/ref/heads/main")]

    # edited in Obsidian: his version replaces the page in the app
    note = files["Wiki/Recherche/Agents IA.md"]
    edited = note.replace("Un fait", "Un fait que j'ai corrigé")
    github.user_commit({"Wiki/Recherche/Agents IA.md": edited})
    res = obsidian.sync_once()
    assert res["imported"] == ["Agents IA"]
    page = _pages()["Agents IA"]
    assert page["updated_by"] == "user" and page["revision"] == 2 and "Un fait que j'ai corrigé" in page["content"]
    assert f"[src:{wiki.ref(item)}]" in page["content"]                   # the source link went back to its code
    rev = db.fetchone("select author, reason from wiki_revisions where page_id = %s and revision = 2", (page["id"],))
    assert rev == {"author": "user", "reason": "obsidian"}
    # the KB's version of the note (frontmatter updated) went back, and the next pass is quiet
    assert 'updated_by: "toi"' in github.files()["Wiki/Recherche/Agents IA.md"]
    assert obsidian.sync_once()["commit"] is None
    assert [e["kind"] for e in wiki.recent_log()[:2]] == ["sync", "edit"]

    # a note moved to another folder changes section, a new note becomes a page, a deleted note leaves the wiki,
    # and SCHEMA.md holds his conventions
    files = github.files()
    github.user_commit({
        "Wiki/Recherche/Andrej Karpathy.md": None,
        "Wiki/Lectures/Andrej Karpathy.md": files["Wiki/Recherche/Andrej Karpathy.md"],
        "Wiki/Lectures/Le Mythe de Sisyphe.md": "# Le Mythe de Sisyphe\n\nCamus : il faut imaginer Sisyphe heureux.",
        "Wiki/Recherche/Évaluation des agents.md": None,
        "SCHEMA.md": "# Schéma du wiki\n\nToujours une section « À retenir ».\n",
        "index.md": "je casse l'index",
    })
    res = obsidian.sync_once()
    assert sorted(res["imported"]) == ["Andrej Karpathy", "Le Mythe de Sisyphe", "SCHEMA.md"]
    assert res["deleted"] == ["Évaluation des agents"]
    pages = _pages()
    assert pages["Andrej Karpathy"]["section"] == "reading"
    assert pages["Le Mythe de Sisyphe"]["content"] == "Camus : il faut imaginer Sisyphe heureux."
    assert pages["Le Mythe de Sisyphe"]["section"] == "reading" and pages["Le Mythe de Sisyphe"]["updated_by"] == "user"
    assert "Évaluation des agents" not in pages
    assert wiki.user_schema() == "Toujours une section « À retenir »."
    files = github.files()
    assert files["index.md"].startswith("# Index du wiki")             # written by the KB, put back
    assert "Wiki/Lectures/Le Mythe de Sisyphe.md" in files and "Wiki/Recherche/Andrej Karpathy.md" not in files


def test_his_edit_wins_over_an_update_he_had_not_seen(client, github, fake_llm):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    old_note = github.files()["Wiki/Recherche/Agents IA.md"]

    # Claude integrates a new item into the page, not yet synced; meanwhile he edits the copy he had
    _add(client, "https://blog.ex.com/agents-2")
    wiki.drain()
    claude_version = _pages()["Agents IA"]
    github.user_commit({"Wiki/Recherche/Agents IA.md": old_note.replace("Un fait", "Mon fait")})
    obsidian.sync_once()
    page = _pages()["Agents IA"]
    assert "Mon fait" in page["content"] and "Les agents en production" not in page["content"]
    assert page["metadata"]["lost_update"]["revision"] == claude_version["revision"]

    # the night's review gets Claude's lost update, to put back the facts he never saw; then the flag goes
    client.post("/api/wiki/lint", headers=AUTH)
    wiki.drain()
    assert "Les agents en production" in fake_llm["wiki_lint"][-1]["lost"]["Agents IA"]
    assert "lost_update" not in _pages()["Agents IA"]["metadata"]


def test_sync_retries_when_someone_pushed_meanwhile(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    # he edits a note; the pass that imports it can't push (someone pushed meanwhile)
    note = github.files()["Wiki/Recherche/Agents IA.md"]
    github.user_commit({"Wiki/Recherche/Agents IA.md": note.replace("Un fait", "Mon fait")})
    github.race = True
    assert obsidian.sync_safely() is None
    assert "nouvel essai" in obsidian.status()["last_error"]
    assert "Mon fait" in _pages()["Agents IA"]["content"]
    # Claude updates the page; the next pass must not import his old edit a second time over it
    _add(client, "https://blog.ex.com/agents-2")
    wiki.drain()
    res = obsidian.sync_once()
    assert res["imported"] == [] and res["commit"]
    page = _pages()["Agents IA"]
    assert "Les agents en production" in page["content"] and "lost_update" not in page["metadata"]
    assert "Les agents en production" in github.files()["Wiki/Recherche/Agents IA.md"]
    assert obsidian.status()["last_error"] is None


def test_first_sync_keeps_notes_already_in_the_repository(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    # a vault that already had notes: the note of a page the wiki has (the KB's version wins, git keeps the old one),
    # a note of a page it doesn't have (taken), and notes that aren't pages' (never touched)
    readme = obsidian.README
    github.blobs[_sha(readme)] = readme
    github.ref = github._commit({"README.md": _sha(readme)}, [], "init")
    github.user_commit({"Wiki/Recherche/Agents IA.md": "Version périmée.",
                        "Wiki/Business/Mes clients.md": "Trois clients plombiers.",
                        "Wiki/Business/Agents IA.md": "Une autre note qui porte ce nom.",
                        "Sources/Mes lectures.md": "Ma liste à moi.",
                        "Journal/2026-10-01.md": "Une note de mon coffre."})
    res = obsidian.sync_once()
    assert res["imported"] == ["Mes clients"]
    assert res["skipped"] == ["Wiki/Business/Agents IA.md"]
    pages = _pages()
    assert pages["Mes clients"]["section"] == "business"
    assert "Version périmée" not in pages["Agents IA"]["content"]
    files = github.files()
    assert "Version périmée" not in files["Wiki/Recherche/Agents IA.md"]
    assert files["Wiki/Business/Agents IA.md"] == "Une autre note qui porte ce nom."
    assert files["Sources/Mes lectures.md"] == "Ma liste à moi."
    assert files["Journal/2026-10-01.md"] == "Une note de mon coffre."
    assert obsidian.status()["skipped"][0]["reason"].endswith("s'appelle déjà « Agents IA »")
    # still untouched by the next passes
    _add(client, "https://blog.ex.com/agents-2")
    wiki.drain()
    obsidian.sync_once()
    files = github.files()
    assert files["Wiki/Business/Agents IA.md"] == "Une autre note qui porte ce nom."
    assert files["Sources/Mes lectures.md"] == "Ma liste à moi."


def test_one_wiki_worker_at_a_time(clean_db):
    """Two containers run side by side during a deploy: only the one holding the lease works the wiki's queue."""
    from app import db

    db.execute("""insert into kb_settings (key, value) values ('wiki_lease', %s)""",
               (db.jsonb({"holder": "other-container", "until": "2999-01-01T00:00:00+00:00"}),))
    assert wiki.acquire_lease() is False
    db.execute("""update kb_settings set value = %s where key = 'wiki_lease'""",
               (db.jsonb({"holder": "other-container", "until": "2000-01-01T00:00:00+00:00"}),))
    assert wiki.acquire_lease() is True and wiki.acquire_lease() is True       # expired: taken, then renewed
    wiki.release_lease()
    assert db.fetchone("select 1 from kb_settings where key = 'wiki_lease'") is None


# ---------------------------------------------------------------------------
# What the review of this feature found: each case, kept
# ---------------------------------------------------------------------------

def test_merging_two_notes_by_hand_in_obsidian(client, github):
    """He deletes one note and renames another to its name: the first page leaves, the second takes the name, and the
    sync goes on (a title clash used to stop it for good)."""
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    files = github.files()
    karpathy = _pages()["Andrej Karpathy"]
    github.user_commit({"Wiki/Recherche/Évaluation des agents.md": files["Wiki/Recherche/Andrej Karpathy.md"],
                        "Wiki/Recherche/Andrej Karpathy.md": None})
    res = obsidian.sync_once()
    assert res["deleted"] == ["Évaluation des agents"] and res["imported"] == ["Évaluation des agents"]
    assert res["skipped"] == []
    pages = _pages()
    assert "Andrej Karpathy" not in pages and pages["Évaluation des agents"]["id"] == karpathy["id"]
    # and what the KB changes afterwards still reaches the repository
    _add(client, "https://blog.ex.com/agents-2")
    wiki.drain()
    assert obsidian.sync_once()["commit"]
    assert "Les agents en production" in github.files()["Wiki/Recherche/Agents IA.md"]


def test_a_copy_of_a_note_is_a_new_page_and_a_clash_is_set_aside(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    note = github.files()["Wiki/Recherche/Agents IA.md"]
    # Obsidian's "Make a copy" keeps the frontmatter (and so the page's kb_id): the copy is a page of its own
    github.user_commit({"Wiki/Recherche/Agents IA 1.md": note.replace("Un fait", "Ma copie")})
    res = obsidian.sync_once()
    assert res["imported"] == ["Agents IA 1"]
    pages = _pages()
    assert "Un fait" in pages["Agents IA"]["content"] and "Ma copie" in pages["Agents IA 1"]["content"]
    assert "Wiki/Recherche/Agents IA.md" in github.files()
    # a second note with a page's name, in another folder: set aside, reported, never touched; the rest goes on
    github.user_commit({"Wiki/Business/Agents IA.md": "Mes notes de prospection sur les agents.",
                        "Wiki/Business/Prospection.md": "Appeler les plombiers le matin."})
    res = obsidian.sync_once()
    assert res["imported"] == ["Prospection"] and res["skipped"] == ["Wiki/Business/Agents IA.md"]
    assert github.files()["Wiki/Business/Agents IA.md"] == "Mes notes de prospection sur les agents."
    assert obsidian.status()["skipped"][0]["path"] == "Wiki/Business/Agents IA.md"
    # renamed by him, it is taken at the next pass and the warning goes
    github.user_commit({"Wiki/Business/Agents IA.md": None,
                        "Wiki/Business/Agents en prospection.md": "Mes notes de prospection sur les agents."})
    res = obsidian.sync_once()
    assert res["imported"] == ["Agents en prospection"] and obsidian.status()["skipped"] == []


def test_what_he_writes_in_a_note_is_kept(client, github):
    """His own frontmatter, a first heading that isn't the title, a subfolder, text under the list of sources."""
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    note = github.files()["Wiki/Recherche/Agents IA.md"]
    head, rest = note.split("---\n", 2)[1:]
    edited = ("---\n" + head + "tags:\n  - ia\n  - agents\naliases: [agents]\n---\n"
              + "# Introduction\n\n" + rest.lstrip("\n").rstrip() + "\n\nMa remarque sous les sources.\n")
    github.user_commit({"Wiki/Recherche/Agents IA.md": None, "Wiki/Recherche/IA/Agents IA.md": edited})
    res = obsidian.sync_once()
    assert res["imported"] == ["Agents IA"]
    page = _pages()["Agents IA"]
    assert page["content"].startswith("# Introduction\n\nSynthèse sur Agents IA.")
    assert page["content"].endswith("Ma remarque sous les sources.")
    assert page["metadata"]["folder"] == "IA"
    files = github.files()
    assert "Wiki/Recherche/Agents IA.md" not in files
    back = files["Wiki/Recherche/IA/Agents IA.md"]
    assert "tags:\n  - ia\n  - agents\naliases: [agents]\n---" in back
    assert back.index("Ma remarque sous les sources.") < back.index(obsidian.MARKER)
    assert obsidian.sync_once()["commit"] is None                       # stable: nothing to write again


def test_review_only_rewrites_what_it_read_at_the_revision_it_read(client, fake_llm, monkeypatch):
    from app import llm

    monkeypatch.setattr(wiki, "LINT_BATCH_CHARS", 1)        # one page per batch
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    wiki.write_from_chat(title="Mes clients", content="Trois plombiers.", section="business")
    answer = wiki.write_from_chat(title="Une réponse", content="Gardée.")
    from app import db
    db.execute("update wiki_pages set kind = 'answer' where id = %s", (answer["id"],))
    lint = llm.wiki_lint

    def lint_meanwhile(**kw):
        out = lint(**kw)
        if kw["pages"][0]["title"] == "Agents IA":
            # while Claude reviews, a page of this batch is rewritten from a conversation
            wiki.write_from_chat(title="Agents IA", content="Écrite pendant la relecture.")
            # and the review sends back a page of another batch
            out["pages"].append({"title": "Mes clients", "section": "research", "kind": "theme", "summary": "x",
                                 "content": "Réécrite depuis la mauvaise relecture.", "reason": "?"})
        if kw["pages"][0]["title"] == "Une réponse":        # an answer sent back as a theme
            out["pages"] = [{"title": "Une réponse", "section": "research", "kind": "theme", "summary": "x",
                             "content": "Gardée, relue.", "reason": "Relue"}]
        return out

    monkeypatch.setattr(llm, "wiki_lint", lint_meanwhile)
    result = wiki.lint()
    pages = _pages()
    assert pages["Agents IA"]["content"] == "Écrite pendant la relecture."          # not overwritten
    assert result["skipped"][0]["title"] == "Agents IA"
    assert "mauvaise relecture" not in pages["Mes clients"]["content"]           # not from another batch
    assert pages["Une réponse"]["kind"] == "answer" and pages["Une réponse"]["content"] == "Gardée, relue."


def test_a_failed_job_whose_item_was_queued_again(client, monkeypatch):
    from app import db, llm

    item = _add(client, "https://blog.ex.com/agents")

    def boom(**kw):
        db.execute("insert into wiki_jobs (kind, item_id) values ('ingest', %s)", (item,))   # reprocessed meanwhile
        raise RuntimeError("Claude indisponible")

    monkeypatch.setattr(llm, "wiki_plan", boom)
    assert wiki.run_once()
    jobs = db.fetchall("select status, result from wiki_jobs order by id")
    assert jobs[0]["status"] == "done" and jobs[0]["result"] == {"superseded": True}
    assert jobs[1]["status"] == "pending"


def test_connector_write_refused_when_the_page_changed_since_read(client):
    from .test_e2e import _mcp

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()

    def call(name, args, rid):
        r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": args}, rid=rid)
        return r.json()["result"]["content"][0]["text"]

    assert "révision 1" in call("read_wiki_page", {"title": "Agents IA"}, 1)
    wiki.write_from_chat(title="Agents IA", content="Une intégration est passée entre-temps.")
    text = call("write_wiki_page", {"title": "Agents IA", "content": "Ma réécriture.", "revision": 1}, 2)
    assert text.startswith("Impossible : La page « Agents IA » a changé depuis ta lecture")
    assert _pages()["Agents IA"]["content"] == "Une intégration est passée entre-temps."
    assert call("write_wiki_page", {"title": "Agents IA", "content": "Ma réécriture.", "revision": 2}, 3) \
        .startswith("Page enregistrée")


def test_output_budget_grows_with_the_pages(monkeypatch):
    from app import llm

    seen = {}
    monkeypatch.setattr(llm, "call_tool", lambda **kw: seen.update(kw) or {"pages": []})
    llm.wiki_write(source="s", ref="abcd1234", pages=[{"title": "A", "section": "research", "kind": "theme",
                                                        "content": "x" * 30_000}], titles=["A"], schema_text="")
    assert seen["max_tokens"] == 2000 + 10_000 + 2500
    llm.wiki_write(source="s", ref="abcd1234", pages=[{"title": "A", "section": "research", "kind": "theme",
                                                        "content": "x" * 200_000}], titles=["A"], schema_text="")
    assert seen["max_tokens"] == llm.WIKI_MAX_TOKENS
    assert "12 000 caractères" in seen["system"]


def test_a_note_of_his_never_replaces_a_page_that_moved_onto_its_name(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    github.user_commit({"Wiki/Business/Agents IA.md": "Mes notes de prospection."})
    assert obsidian.sync_once()["skipped"] == ["Wiki/Business/Agents IA.md"]
    # Claude moves the page to Business, where his note sits: the page stays in its old note, kept up to date
    wiki.write_from_chat(title="Agents IA", content="Les agents, vus côté business.", section="business")
    obsidian.sync_once()
    files = github.files()
    assert files["Wiki/Business/Agents IA.md"] == "Mes notes de prospection."
    assert "Les agents, vus côté business." in files["Wiki/Recherche/Agents IA.md"]
    assert "reste dans « Wiki/Recherche/Agents IA.md »" in obsidian.status()["skipped"][0]["reason"]
    # he edits his own note: it still isn't taken for the page
    github.user_commit({"Wiki/Business/Agents IA.md": "Mes notes de prospection, suite."})
    obsidian.sync_once()
    assert _pages()["Agents IA"]["content"] == "Les agents, vus côté business."
    assert github.files()["Wiki/Business/Agents IA.md"] == "Mes notes de prospection, suite."


def test_a_note_pasted_over_another_is_an_edit_of_that_page(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    files = github.files()
    before = _pages()
    github.user_commit({"Wiki/Recherche/Évaluation des agents.md": files["Wiki/Recherche/Andrej Karpathy.md"]})
    res = obsidian.sync_once()
    assert res["deleted"] == [] and res["imported"] == ["Évaluation des agents"]
    pages = _pages()
    assert pages["Évaluation des agents"]["id"] == before["Évaluation des agents"]["id"]      # the same page
    assert pages["Évaluation des agents"]["content"] == before["Andrej Karpathy"]["content"]
    assert pages["Andrej Karpathy"]["revision"] == before["Andrej Karpathy"]["revision"]
    assert f'kb_id: "{before["Évaluation des agents"]["id"]}"' in github.files()["Wiki/Recherche/Évaluation des agents.md"]


def test_a_note_set_aside_is_taken_once_the_clash_is_gone(client, github):
    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    obsidian.sync_once()
    github.user_commit({"Wiki/Business/Agents IA.md": "Mes notes de prospection."})
    assert obsidian.sync_once()["skipped"] == ["Wiki/Business/Agents IA.md"]
    github.user_commit({"Wiki/Recherche/Agents IA.md": None})          # he deletes the page's own note
    res = obsidian.sync_once()
    assert res["deleted"] == ["Agents IA"] and res["imported"] == ["Agents IA"] and res["skipped"] == []
    page = _pages()["Agents IA"]
    assert page["section"] == "business" and page["content"] == "Mes notes de prospection."
    assert obsidian.status()["skipped"] == []


def test_a_summary_written_over_several_lines():
    meta, extra, body = obsidian.parse_note('---\nkb_id: "x"\nsummary: >\n  Ce que couvre\n  la page.\ntags: [a]\n---\n\nTexte.')
    assert meta == {"kb_id": "x", "summary": "Ce que couvre la page."}
    assert extra == "tags: [a]" and body == "Texte."


def test_a_review_that_loses_the_lease_is_not_recorded(client, monkeypatch):
    from app import db

    _add(client, "https://blog.ex.com/agents")
    wiki.drain()
    job = wiki.enqueue("lint")
    monkeypatch.setattr(wiki, "acquire_lease", lambda seconds=wiki.LEASE_SECONDS: False)
    assert wiki.run_once()
    assert db.fetchone("select status, error from wiki_jobs where id = %s", (job,))["status"] == "pending"
    assert wiki.last_lint() is None and wiki.suggestions()["items"] == []
