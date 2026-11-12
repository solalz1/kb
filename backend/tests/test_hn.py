"""The Hacker News space: the day's best stories, kept apart from what the user saves."""

import pytest

from app import db, migrate
from app.config import get_settings
from app.taxonomy import normalize_space, scope_spaces


def test_hn_is_a_space():
    assert normalize_space("HN") == normalize_space("hacker news") == normalize_space("hackernews") == "hn"
    assert scope_spaces("mine") == ["main", "perso"]
    assert scope_spaces("hn") == ["hn"]
    assert scope_spaces("both") is None and scope_spaces(None) is None
    with pytest.raises(ValueError):
        scope_spaces("everything")


def test_items_accept_the_hn_space(clean_db):
    migrate.run()  # the schema is applied once by the session fixture: running it again must change nothing
    db.execute("insert into items (input_url, space) values ('https://a.example.com', 'hn')")
    with pytest.raises(Exception):
        db.execute("insert into items (input_url, space) values ('https://b.example.com', 'nope')")


# ---------------------------------------------------------------------------
# HN items stay out of what the user owns

from fastapi.testclient import TestClient  # noqa: E402

from app import extractors, folders, pipeline, urls  # noqa: E402
from app.extractors.base import Extracted, ExtractionError  # noqa: E402

from .conftest import drain  # noqa: E402
from .test_e2e import AUTH  # noqa: E402


@pytest.fixture
def hn_client(clean_db, fake_llm, monkeypatch):
    """The API, with the pages a test registers (`client.pages`, by canonical URL) as the only readable web."""
    pages: dict[str, Extracted] = {}

    def fake_extract_url(url):
        page = pages.get(urls.classify(url).canonical)
        if page is None:
            raise ExtractionError("Page illisible")
        return page

    monkeypatch.setattr(extractors, "extract_url", fake_extract_url)
    fake_hn(monkeypatch, [])       # no test reaches Hacker News
    from app.main import app

    with TestClient(app) as c:
        c.pages = pages
        yield c


def fake_hn(monkeypatch, stories, comments=None, fail=None):
    """Hacker News from dicts: `stories` are Algolia hits (the search honours `numericFilters`), `comments` maps a
    story id to its top-level comments, in HN's order: (author, html) or a dict of Firebase fields ("deleted",
    "dead"…). `fail`: an exception every call raises. Returns the list of URLs asked."""
    from app.extractors import hackernews

    comments = comments or {}
    asked = []

    def kids(story_id):
        out = []
        for n, c in enumerate(comments.get(story_id, [])):
            c = {"by": c[0], "text": c[1]} if isinstance(c, tuple) else c
            out.append({"id": int(f"{story_id}{n:03d}"), "parent": int(story_id), "type": "comment", **c})
        return out

    def get_json(url, params=None):
        asked.append(url)
        if fail:
            raise fail
        if url.endswith("/search"):
            floor = {k: int(v) for k, v in (f.split(">") for f in params["numericFilters"].split(","))}
            hits = [h for h in stories if h["created_at_i"] > floor["created_at_i"] and h["points"] > floor["points"]]
            return {"hits": hits[: int(params.get("hitsPerPage", 20))]}
        story_id = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".json")
        if "firebaseio" in url:
            return {"id": int(story_id), "type": "story", "kids": [c["id"] for c in kids(story_id)]} \
                if story_id in {h["objectID"] for h in stories} else next(
                    (c for cs in comments for c in kids(cs) if str(c["id"]) == story_id), None)
        # like Algolia: a deleted comment keeps its place without author or text, a dead one isn't there
        return {"id": int(story_id), "children": [
            {"id": c["id"], "author": None if c.get("deleted") else c.get("by"),
             "text": None if c.get("deleted") else c.get("text")}
            for c in kids(story_id) if not c.get("dead")]}

    monkeypatch.setattr(hackernews, "get_json", get_json)
    return asked


def _page(client, url, body, title="Une page"):
    client.pages[urls.classify(url).canonical] = Extracted(kind="article", title=title, source_url=url, content=body)


def _hn_meta(**hn):
    story = hn.pop("id", "1")
    return {"id": story, "points": 100, "comments": 10, "author": "pg",
            "discussion_url": f"https://news.ycombinator.com/item?id={story}",
            "posted_at": "2026-11-12T05:00:00+00:00", **hn}


def _hn_item(client, url, body="Les agents autonomes orchestrés changent la recherche documentaire. " * 4, **hn):
    _page(client, url, body, title="Une story HN")
    res = pipeline.ingest(url=url, space="hn", hn=_hn_meta(**hn))
    drain()
    return res["id"]


def _saved(client, url, body="Les agents autonomes orchestrés changent la recherche documentaire. " * 4, space=None):
    _page(client, url, body, title="Une page gardée")
    r = client.post("/api/ingest", json={"url": url, **({"space": space} if space else {})}, headers=AUTH)
    assert r.status_code == 200, r.text
    drain()
    return r.json()["id"]


def test_hn_item_is_not_filed_wikied_or_todo(hn_client, fake_llm):
    folders.create("Agents")
    saved = _saved(hn_client, "https://blog.example.com/agents")
    iid = _hn_item(hn_client, "https://news.example.com/a", id="1")
    row = db.fetchone("select folder_id, status, metadata from items where id = %s", (iid,))
    assert row["status"] == "ready" and row["folder_id"] is None
    assert row["metadata"]["hn"]["points"] == 100
    assert fake_llm["enrich"][-1]["folders"] is None
    assert not db.fetchone("select 1 from wiki_jobs where item_id = %s", (iid,))
    assert not db.fetchone("select 1 from actions where item_id = %s", (iid,))
    assert not db.fetchone("select 1 from item_links where source_id = %s or target_id = %s", (iid, iid))
    # the same words saved by the user: filed, wikied, with its to-dos
    assert db.fetchone("select folder_id from items where id = %s", (saved,))["folder_id"]
    assert db.fetchone("select 1 from wiki_jobs where item_id = %s", (saved,))
    assert db.fetchone("select 1 from actions where item_id = %s", (saved,))


def test_saved_items_never_link_to_hn(hn_client):
    hn_id = _hn_item(hn_client, "https://news.example.com/a", id="1")
    other = _saved(hn_client, "https://blog.example.com/other")
    saved = _saved(hn_client, "https://blog.example.com/agents")
    assert db.fetchone("select 1 from item_links where source_id = %s and target_id = %s", (saved, other))
    assert not db.fetchone("select 1 from item_links where %s in (source_id, target_id)", (hn_id,))
    related = hn_client.get(f"/api/items/{saved}", headers=AUTH).json()["links"]
    assert related and hn_id not in {r["id"] for r in related}


def test_hn_is_left_out_of_counts_and_suggestions(hn_client):
    from app.config import Settings
    from app.digest import sources
    from app.mcp_server import resurface_items

    _saved(hn_client, "https://garden.example.com/compost", body="Jardinage potager compost paillage. " * 6)
    hn_id = _hn_item(hn_client, "https://news.example.com/quantum", id="2",
                     body="Ordinateur quantique supraconducteur cryogénie. " * 6)
    hn_only = set(db.fetchone("select tags from items where id = %s", (hn_id,))["tags"])
    assert hn_only

    stats = hn_client.get("/api/stats", headers=AUTH).json()
    assert stats["total"] == 1 and stats["this_week"] == 1 and sum(k["n"] for k in stats["by_kind"]) == 1
    assert stats["by_space"] == {"main": 1, "hn": 1}
    assert not hn_only & {t["tag"] for t in hn_client.get("/api/tags", headers=AUTH).json()}
    assert hn_only <= {t["tag"] for t in hn_client.get("/api/tags", params={"space": "hn"}, headers=AUTH).json()}
    assert {e["count"] for e in hn_client.get("/api/entities", headers=AUTH).json()} == {1}
    assert not hn_only & set(pipeline.existing_tags())

    db.execute("update items set created_at = now() - interval '30 days'")
    assert {r["space"] for r in resurface_items(5)} == {"main"}
    assert sources.already_in_kb(["https://news.example.com/quantum"]) == set()
    assert folders.overview()["unfiled"] == 1
    assert Settings(notion_spaces="main,hn").notion_space_list == ["main"]


# ---------------------------------------------------------------------------
# Search scopes, HN capped at half

from app import search  # noqa: E402
from app.formatting import item_meta_line  # noqa: E402
from app.search import cap_hn  # noqa: E402

HN_VECTOR = "Vector databases: vector databases benchmarks for vector databases at scale. " * 4
SAVED_VECTOR = "Notes de lecture sur les vector databases et le choix d'un index pour mon projet. " * 3


def _vector_kb(client, hn_count=10):
    hn_ids = [_hn_item(client, f"https://news.example.com/v{i}", body=HN_VECTOR + f" Story {i}.", id=str(100 + i))
              for i in range(hn_count)]
    saved = [_saved(client, "https://blog.example.com/vdb", body=SAVED_VECTOR),
             _saved(client, "https://blog.example.com/vdb2", body=SAVED_VECTOR + " Deuxième.")]
    perso = _saved(client, "https://perso.example.com/vdb", body=SAVED_VECTOR + " Une leçon.", space="perso")
    return hn_ids, saved, perso


def test_cap_hn():
    ranked = [{"id": str(i), "space": "hn"} for i in range(6)] + [{"id": "s", "space": "main"}]
    assert [r["id"] for r in cap_hn(ranked, 4)] == ["0", "1", "s"]
    assert [r["id"] for r in cap_hn([{"id": "s", "space": "main"}] * 3, 2)] == ["s", "s"]


def test_mixed_search_keeps_saved_items(hn_client):
    _vector_kb(hn_client)
    found = search.search_items("vector databases", limit=8)
    assert sum(f["space"] == "hn" for f in found) <= 4
    assert {"main", "perso"} <= {f["space"] for f in found}
    assert {f["space"] for f in search.search_items("vector databases", spaces=["hn"])} == {"hn"}


def test_items_search_scope(hn_client):
    _vector_kb(hn_client, hn_count=3)
    for scope, spaces in {"hn": {"hn"}, "mine": {"main", "perso"}, "main": {"main"}, "perso": {"perso"},
                          "all": {"main", "perso", "hn"}}.items():
        r = hn_client.get("/api/items", params={"q": "vector databases", "in": scope}, headers=AUTH).json()
        assert r["items"] and {i["space"] for i in r["items"]} == spaces, scope
    # the scope wins over the page's space
    r = hn_client.get("/api/items", params={"q": "vector databases", "space": "main", "in": "hn"}, headers=AUTH)
    assert {i["space"] for i in r.json()["items"]} == {"hn"}
    assert r.json()["items"][0]["hn"]["points"] == 100
    assert hn_client.get("/api/items", params={"q": "x", "in": "nowhere"}, headers=AUTH).status_code == 400


def test_folder_scope_searches_saved_items_only(hn_client):
    folder = folders.create("Vector")
    _vector_kb(hn_client, hn_count=2)
    db.execute("update items set folder_id = %s::uuid", (folder["id"],))   # even an HN item put there by mistake
    for params in ({"in": f"folder:{folder['id']}"}, {"folder": folder["id"]}):
        r = hn_client.get("/api/items", params={"q": "vector databases", **params}, headers=AUTH).json()
        assert r["items"] and "hn" not in {i["space"] for i in r["items"]}


def test_meta_line_says_hacker_news():
    line = item_meta_line({"kind": "article", "space": "hn", "metadata": {"hn": {"points": 512, "comments": 230}}})
    assert line.startswith("Hacker News · 512 points · 230 commentaires")


# ---------------------------------------------------------------------------
# The daily run

from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app import hn  # noqa: E402

NOW = datetime(2026, 11, 12, 6, 30, tzinfo=ZoneInfo("Europe/Paris"))


def _hit(i, **kw):
    return {"objectID": str(1000 + i), "title": f"Story {i} about compilers", "url": f"https://story{i}.example.com/post",
            "points": 41 + i, "num_comments": 5 + i, "author": f"user{i}",
            "created_at_i": int((NOW - timedelta(minutes=45 * i)).timestamp()), "story_text": None, **kw}


# 40 stories posted over the last 30 hours (points 41…80), the best one an Ask HN, plus one too old
STORIES = [_hit(i) for i in range(39)] + [_hit(39, title="Ask HN: How do you read papers?", url=None,
                                               story_text="<p>I struggle &amp; wonder.</p>")]
TOO_OLD = _hit(60, points=900, created_at_i=int((NOW - timedelta(hours=37)).timestamp()))


def _hn_rows():
    return db.fetchall("select id::text, space, metadata->'hn' as hn from items where metadata ? 'hn'")


def test_run_adds_the_30_best(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES + [TOO_OLD])
    assert hn.run(NOW) == {"added": 30, "skipped": 0}
    rows = _hn_rows()
    assert len(rows) == 30 and {r["space"] for r in rows} == {"hn"}
    assert min(r["hn"]["points"] for r in rows) == 51
    assert TOO_OLD["objectID"] not in {r["hn"]["id"] for r in rows}
    ask = next(r["hn"] for r in rows if r["hn"]["id"] == "1039")
    assert set(ask) >= {"id", "points", "comments", "author", "discussion_url", "posted_at", "text"}
    assert ask["discussion_url"] == "https://news.ycombinator.com/item?id=1039" and ask["comments"] == 44
    assert db.fetchone("select input_url from items where metadata->'hn'->>'id' = '1039'")["input_url"] \
        == "https://news.ycombinator.com/item?id=1039"
    assert hn.run(NOW + timedelta(hours=1)) == {"added": 0, "skipped": 0}      # at most 30 a day


def test_run_due_once_a_day(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES)
    assert hn.run_due(NOW.replace(hour=5)) is None
    assert hn.run_due(NOW.replace(hour=6))["added"] == 30
    assert hn.run_due(NOW.replace(hour=7)) is None
    monkeypatch.setattr(get_settings(), "hn_enabled", False)
    assert hn.run_due(NOW + timedelta(days=1)) is None


def test_failed_run_retries(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES, fail=RuntimeError("HN is down"))
    with pytest.raises(RuntimeError):
        hn.run_due(NOW)
    fake_hn(monkeypatch, STORIES)
    assert hn.run_due(NOW + timedelta(minutes=1))["added"] == 30


def test_already_saved_article_is_skipped(hn_client, monkeypatch):
    saved = _saved(hn_client, "https://blog.example.com/x")
    fake_hn(monkeypatch, STORIES + [_hit(70, points=900, url="https://blog.example.com/x?utm_source=hn",
                                                 created_at_i=STORIES[0]["created_at_i"])])
    assert hn.run(NOW) == {"added": 30, "skipped": 1}
    assert "1070" not in {r["hn"]["id"] for r in _hn_rows()}
    assert db.fetchone("select space from items where id = %s", (saved,))["space"] == "main"


def test_deleted_story_is_not_readded(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES)
    hn.run(NOW)
    gone = next(r for r in _hn_rows() if r["hn"]["id"] == "1012")      # posted 9 hours before the first run
    assert hn_client.delete(f"/api/items/{gone['id']}", headers=AUTH).status_code == 200
    assert hn.run(NOW + timedelta(days=1))["added"] == 10             # the 10 left in the window, not the deleted one
    assert "1012" not in {r["hn"]["id"] for r in _hn_rows()}


def test_concurrent_run_is_busy(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES)
    with hn._lock:
        assert hn.run(NOW) == {"busy": True}
    assert not _hn_rows()


def test_run_endpoint(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES)
    monkeypatch.setattr(hn, "_now", lambda: NOW)
    assert hn_client.post("/api/hn/run", headers=AUTH).json() == {"added": 30, "skipped": 0}



# ---------------------------------------------------------------------------
# A card holds the article and its discussion

import httpx  # noqa: E402

from app.extractors import hackernews  # noqa: E402


def _card():
    return db.fetchone("select status, title, content, source_url, site_name, metadata, error from items "
                       "where space = 'hn'")


def test_card_has_article_then_discussion(hn_client, monkeypatch):
    _page(hn_client, STORIES[0]["url"], "Article body about compilers and their passes. " * 5, title="Compilers")
    fake_hn(monkeypatch, STORIES[:1], comments={"1000": [("alice", "<p>Great &amp; fast</p>"), ("bob", "Meh")]})
    hn.run(NOW)
    drain()
    card = _card()
    assert card["status"] == "ready", card["error"]
    content = card["content"]
    assert content.index("Article body") < content.index("## Discussion Hacker News")
    assert "41 points · 5 commentaires · https://news.ycombinator.com/item?id=1000" in content
    assert content.index("**alice** : Great & fast") < content.index("**bob** : Meh")
    assert card["metadata"]["hn"]["points"] == 41 and "article_unreadable" not in card["metadata"]["hn"]


def test_ask_hn_card_is_post_then_discussion(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES[39:], comments={"1039": [("carol", "Read the abstract first.")]})
    hn.run(NOW)
    drain()
    card = _card()
    assert card["status"] == "ready", card["error"]
    assert card["content"].startswith("I struggle & wonder.")
    assert card["content"].index("I struggle") < card["content"].index("**carol** : Read the abstract first.")
    assert card["title"] == "Ask HN: How do you read papers?"
    assert card["source_url"] == "https://news.ycombinator.com/item?id=1039" and card["site_name"] == "Hacker News"


def test_unreadable_article_still_makes_a_card(hn_client, monkeypatch):
    fake_hn(monkeypatch, STORIES[:1], comments={"1000": [("alice", "The paywall again.")]})   # no page: unreadable
    hn.run(NOW)
    drain()
    card = _card()
    assert card["status"] == "ready", card["error"]
    assert card["metadata"]["hn"]["article_unreadable"] is True and card["metadata"]["hn"]["points"] == 41
    assert card["title"] == "Story 0 about compilers"
    assert card["source_url"] == "https://story0.example.com/post"
    assert "**alice** : The paywall again." in card["content"]


def test_discussion_text_is_clean(monkeypatch):
    fake_hn(monkeypatch, [_hit(0)], comments={"1000": [
        ("alice", "<p>Great &amp; fast</p>"),
        {"deleted": True},
        {"by": "carol", "text": "A dead comment", "dead": True},
        ("dave", 'See <a href="https://ref.example.com/a/b" rel="nofollow">https://ref.example.com/a/...</a>'
                 "<p>It&#x27;s <i>really</i> &quot;good&quot; &gt; 2"),
        ("erin", ""),
        ("frank", "x" * 2000),
        ("gina", "Past the limit"),
    ]})
    comments = hackernews.discussion("1000", limit=3)
    assert [c["author"] for c in comments] == ["alice", "dave", "frank"]
    assert comments[0]["text"] == "Great & fast"
    assert comments[1]["text"] == 'See https://ref.example.com/a/b\n\nIt\'s *really* "good" > 2'
    assert len(comments[2]["text"]) <= 1500 and comments[2]["text"].endswith("…")


def test_network_error_is_retried(hn_client, monkeypatch):
    _page(hn_client, STORIES[0]["url"], "Article body. " * 20)
    fake_hn(monkeypatch, STORIES[:1])
    hn.run(NOW)
    fake_hn(monkeypatch, STORIES[:1], fail=httpx.ConnectError("HN is down"))
    drain()
    card = _card()
    assert card["status"] == "pending" and "ConnectError" in card["error"]


# ---------------------------------------------------------------------------
# Keeping an HN card in Veille

def _kept(iid):
    return db.fetchone("""select space, folder_id::text, exists(select 1 from wiki_jobs where item_id = items.id) as wiki
                            from items where id = %s""", (iid,))


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(pipeline, "_background", lambda fn, *a: fn(*a))


def test_sharing_an_hn_link_keeps_it(hn_client, inline):
    folder = folders.create("Agents")
    iid = _hn_item(hn_client, "https://news.example.com/rag", id="7")
    other = _saved(hn_client, "https://blog.example.com/agents")
    r = hn_client.post("/api/ingest", json={"url": "https://news.example.com/rag?utm_source=hn"}, headers=AUTH).json()
    assert r["id"] == iid and r["items"][0]["duplicate"] is True and r["message"] == "Gardé dans ta Veille ✓"
    assert _kept(iid) == {"space": "main", "folder_id": folder["id"], "wiki": True}
    assert db.fetchone("select 1 from item_links where source_id = %s and target_id = %s", (iid, other))
    assert db.fetchone("select metadata->'hn'->>'id' as id from items where id = %s", (iid,))["id"] == "7"


def test_keep_in_veille_from_the_app(hn_client, inline):
    folder = folders.create("Agents")
    iid = _hn_item(hn_client, "https://news.example.com/rag", id="7")
    assert hn_client.patch(f"/api/items/{iid}", json={"space": "main"}, headers=AUTH).status_code == 200
    assert _kept(iid) == {"space": "main", "folder_id": folder["id"], "wiki": True}
    assert "Agents" in {f["name"] for f in folders.all_folders() if f["count"]}


def test_kept_while_it_was_being_read(hn_client, inline, monkeypatch):
    """« Garder » pressed while the worker reads the story: it ends up filed and in the wiki all the same."""
    folders.create("Agents")
    _page(hn_client, "https://news.example.com/rag", "Les agents autonomes orchestrés. " * 8)
    res = pipeline.ingest(url="https://news.example.com/rag", space="hn", hn=_hn_meta(id="7"))
    real = extractors.extract_url

    def read_and_keep(url):
        db.execute("update items set space = 'main' where id = %s", (res["id"],))
        return real(url)

    monkeypatch.setattr(extractors, "extract_url", read_and_keep)
    drain()
    assert _kept(res["id"])["folder_id"] and _kept(res["id"])["wiki"]


def test_nothing_is_moved_into_hn_by_hand(hn_client):
    saved = _saved(hn_client, "https://blog.example.com/agents")
    assert hn_client.patch(f"/api/items/{saved}", json={"space": "hn"}, headers=AUTH).status_code == 400
    assert hn_client.post("/api/ingest", json={"url": "https://blog.example.com/new", "space": "hn"},
                          headers=AUTH).status_code == 400
    assert db.fetchone("select space from items where id = %s", (saved,))["space"] == "main"


# ---------------------------------------------------------------------------
# Chat scopes

from app import wiki  # noqa: E402

from .test_perso import _events  # noqa: E402


def _chat(client, **body):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "vector databases"}], **body},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    events = _events(r)
    assert not [e for e in events if e["type"] == "error"], events
    return [s for e in events if e["type"] == "sources" for s in e["sources"]]


@pytest.mark.parametrize("scope,allowed", [("mine", {"main", "perso"}), ("hn", {"hn"}),
                                           ("both", {"main", "perso", "hn"})])
def test_ask_scope(hn_client, scope, allowed):
    _vector_kb(hn_client)
    sources = _chat(hn_client, source="rag", scope=scope)
    assert {s["space"] for s in sources} == allowed
    if scope == "both":
        assert sum(s["space"] == "hn" for s in sources) <= 4        # half of the 8 places


def test_ask_scope_hn_ignores_the_wiki(hn_client):
    _vector_kb(hn_client, hn_count=3)
    assert {s["space"] for s in _chat(hn_client, source="wiki", scope="hn")} == {"hn"}


def test_unknown_scope(hn_client):
    r = hn_client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "scope": "partout"},
                       headers=AUTH)
    assert r.status_code == 400


def test_wiki_answer_with_hn(hn_client):
    for i in range(2):
        _saved(hn_client, f"https://blog.example.com/agents{i}", body=f"Les agents autonomes, version {i}. " * 6)
    wiki.drain()
    for i in range(6):
        _hn_item(hn_client, f"https://news.example.com/agents{i}", id=str(300 + i),
                 body=f"Agents autonomes en production, retour {i}. " * 6)
    pages = [p["title"] for p in wiki.all_pages()]
    assert "Agents IA" in pages
    msg = {"messages": [{"role": "user", "content": "Que sais-je des agents autonomes ?"}]}
    r = hn_client.post("/api/chat", json={**msg, "source": "wiki", "scope": "both"}, headers=AUTH)
    sources = [s for e in _events(r) if e["type"] == "sources" for s in e["sources"]]
    kinds = [s["kind"] for s in sources]
    wiki_cards = kinds.count("wiki")
    assert wiki_cards and kinds[:wiki_cards] == ["wiki"] * wiki_cards      # the wiki's pages first
    hn_cards = [s for s in sources if s["kind"] != "wiki"]
    assert hn_cards and {s["space"] for s in hn_cards} == {"hn"} and len(hn_cards) <= wiki_cards
    assert [s["n"] for s in sources] == list(range(1, len(sources) + 1))
    # the same question on the saves only: the wiki, no HN
    r = hn_client.post("/api/chat", json={**msg, "source": "wiki", "scope": "mine"}, headers=AUTH)
    assert {s["kind"] for e in _events(r) if e["type"] == "sources" for s in e["sources"]} == {"wiki"}


def test_project_scope_mine(hn_client):
    _vector_kb(hn_client)
    sources = _chat(hn_client, mode="project", scope="mine")
    assert sources and "hn" not in {s["space"] for s in sources}
    assert "hn" in {s["space"] for s in _chat(hn_client, mode="project", scope="both")}


def test_advice_never_sees_hn(hn_client):
    _vector_kb(hn_client)
    sources = _chat(hn_client, mode="advice", scope="both")
    assert sources and "hn" not in {s["space"] for s in sources}


def test_system_prompt_tells_hn_apart(hn_client, fake_llm):
    _vector_kb(hn_client, hn_count=2)
    _chat(hn_client, source="rag", scope="both")
    assert "Hacker News" in fake_llm["stream"][-1]["system"]
