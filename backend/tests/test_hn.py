"""The Hacker News space: the day's best stories, kept apart from what the user saves."""

import pytest

from app import db, migrate
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
    from app.main import app

    with TestClient(app) as c:
        c.pages = pages
        yield c


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
