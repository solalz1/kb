"""Reddit posts: Reddit refuses servers, so the Shortcut sends the post's page from the phone and the KB reads it.
The pages below are fictional, in the shapes reddit.com, old.reddit.com and search engines (schema.org) get."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import extractors, urls
from app.extractors import ExtractionError, reddit, web

from .conftest import drain
from .test_blocked_sites import _fake_web, _page_upload
from .test_e2e import AUTH

SHARE = "https://reddit.com/r/exemple_ia/s/Xy12AbCd"           # what the Reddit app shares
POST = "https://www.reddit.com/r/exemple_ia/comments/1abcde2/mes_regles_pour_claude/"
CANONICAL = "https://www.reddit.com/r/exemple_ia/comments/1abcde2"

# what Railway gets from Reddit (and what Jina Reader got for it): the KB once summarized this page as the post
BLOCK_PAGE = """![Image 1](blob:http://localhost/7c466567)

You've been blocked by network security.

To continue, log in to your Reddit account or use your developer token

If you think you've been blocked by mistake, file a ticket below and we'll look into it.

[Log in](https://www.reddit.com/login/)[File a ticket](https://support.reddithelp.com/hc/en-us/requests/new)"""

SHREDDIT = """<!doctype html><html><head><title>Mes règles pour Claude : r/exemple_ia</title>
<link rel="canonical" href="https://www.reddit.com/r/exemple_ia/comments/1abcde2/mes_regles_pour_claude/"></head>
<body><shreddit-app>
<shreddit-post id="t3_1abcde2" post-title="Mes règles pour Claude après six mois" author="alice_dev"
  subreddit-prefixed-name="r/exemple_ia" created-timestamp="2026-03-02T09:15:00.000000+0000"
  permalink="/r/exemple_ia/comments/1abcde2/mes_regles_pour_claude/"
  content-href="https://www.reddit.com/r/exemple_ia/comments/1abcde2/mes_regles_pour_claude/" post-type="text">
  <div slot="text-body"><div class="md">
    <p>Après six mois, voici ce qui marche le mieux pour moi.</p>
    <ul><li><p>Donner un exemple de sortie attendue.</p></li><li>Demander un plan avant le code.</li></ul>
    <p>Le reste compte beaucoup moins.<br>Bonne journée.</p>
  </div></div>
</shreddit-post>
<shreddit-comment-tree>
  <shreddit-comment author="bob_ml" depth="0" thingid="t1_aaa">
    <div slot="comment"><div class="md"><p>Le plan avant le code, c'est le meilleur conseil.</p></div></div>
    <shreddit-comment author="carol" depth="1" thingid="t1_bbb">
      <div slot="comment"><div class="md"><p>Une réponse à une réponse.</p></div></div>
    </shreddit-comment>
  </shreddit-comment>
  <shreddit-comment author="dave" depth="0" thingid="t1_ccc">
    <div slot="comment"><div class="md"><p>Je fais pareil avec des tests écrits d'abord.</p></div></div>
  </shreddit-comment>
</shreddit-comment-tree>
</shreddit-app></body></html>"""

JSON_LD = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "DiscussionForumPosting",
 "headline": "Un lien vers un bon papier sur l'éval", "url": "https://www.reddit.com/r/exemple_ml/comments/9zyxw8/papier/",
 "author": {"@type": "Person", "name": "eve_research"}, "datePublished": "2026-02-10T18:00:00+00:00",
 "sharedContent": {"@type": "WebPage", "url": "https://arxiv.org/abs/2601.00001"},
 "comment": [{"@type": "Comment", "text": "Merci, je cherchais ça.", "author": {"name": "frank"}}]}
</script></head><body><p>Chargement…</p></body></html>"""

OLD_REDDIT = """<html><body><div id="siteTable">
<div class="thing id-t3_7qwert self link" data-fullname="t3_7qwert" data-author="gina_dev"
  data-subreddit-prefixed="r/exemple_dev" data-timestamp="1767225600000"
  data-permalink="/r/exemple_dev/comments/7qwert/old_reddit/" data-url="/r/exemple_dev/comments/7qwert/old_reddit/">
  <p class="title"><a class="title" href="/r/exemple_dev/comments/7qwert/old_reddit/">Old reddit, toujours là</a></p>
  <div class="expando"><form><div class="usertext-body"><div class="md"><p>Un post lu sur old.reddit.com.</p></div></div></form></div>
</div></div>
<div class="commentarea"><div class="sitetable nestedlisting">
  <div class="thing comment" data-author="hugo"><div class="entry"><form><div class="usertext-body"><div class="md">
    <p>Toujours plus rapide.</p></div></div></form></div></div>
</div></div></body></html>"""


def test_links_to_a_post_give_one_address():
    for url in (POST, "https://old.reddit.com/r/exemple_ia/comments/1abcde2/", "https://np.reddit.com/r/exemple_ia/comments/1ABCDE2",
                "https://www.reddit.com/r/exemple_ia/comments/1abcde2/mes_regles_pour_claude/c1zz9x/?utm_source=share",
                "https://m.reddit.com/r/exemple_ia/comments/1abcde2/x/comment/c1zz9x/"):
        info = urls.classify(url)
        assert (info.kind, info.canonical, info.ids) == ("web", CANONICAL, {"reddit_post": "1abcde2"}), url
    assert urls.classify("https://redd.it/1abcde2").canonical == "https://www.reddit.com/comments/1abcde2"
    # a share link only redirects: kept as it is (the phone follows it)
    assert urls.classify(SHARE) == urls.UrlInfo("web", "https://reddit.com/r/exemple_ia/s/Xy12AbCd", {})
    assert urls.is_reddit(SHARE) and urls.is_reddit("redd.it/abc") and not urls.is_reddit("https://notreddit.com/x")
    assert urls.dedupe_filter(urls.classify(POST)) == ("source_url like %s", ["%reddit.com/%comments/1abcde2"])


def test_a_post_as_reddit_com_renders_it():
    ex = reddit.parse_html(SHREDDIT, SHARE, SHARE)
    assert ex.title == "Mes règles pour Claude après six mois"
    assert ex.author == "u/alice_dev" and ex.author_url == "https://www.reddit.com/user/alice_dev"
    assert ex.site_name == "Reddit · r/exemple_ia" and ex.published_at.isoformat() == "2026-03-02T09:15:00+00:00"
    assert ex.source_url == CANONICAL                    # the share link resolved to the post
    assert ex.content.startswith("r/exemple_ia · u/alice_dev · 2026-03-02\n\n# Mes règles pour Claude après six mois")
    assert ("Après six mois, voici ce qui marche le mieux pour moi.\n\n- Donner un exemple de sortie attendue.\n\n"
            "- Demander un plan avant le code.\n\nLe reste compte beaucoup moins.\nBonne journée.") in ex.content
    assert ex.content.endswith("## Commentaires\n\n- u/bob_ml : Le plan avant le code, c'est le meilleur conseil.\n"
                               "- u/dave : Je fais pareil avec des tests écrits d'abord.")
    assert "Une réponse à une réponse" not in ex.content           # top-level comments only
    assert "Lien partagé" not in ex.content                        # a text post links to itself
    assert ex.metadata == {"subreddit": "r/exemple_ia", "reddit_post": "1abcde2", "comments_read": 2}


def test_a_link_post_described_for_search_engines():
    ex = reddit.parse_html(JSON_LD, "https://redd.it/9zyxw8", "https://www.reddit.com/comments/9zyxw8")
    assert ex.title == "Un lien vers un bon papier sur l'éval" and ex.author == "u/eve_research"
    assert ex.source_url == "https://www.reddit.com/r/exemple_ml/comments/9zyxw8"
    assert "Lien partagé : https://arxiv.org/abs/2601.00001" in ex.content
    assert "- u/frank : Merci, je cherchais ça." in ex.content


def test_a_post_on_old_reddit():
    ex = reddit.parse_html(OLD_REDDIT, "https://old.reddit.com/r/exemple_dev/comments/7qwert/old_reddit/", "")
    assert ex.title == "Old reddit, toujours là" and ex.site_name == "Reddit · r/exemple_dev"
    assert ex.published_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert "Un post lu sur old.reddit.com." in ex.content and "- u/hugo : Toujours plus rapide." in ex.content


def test_a_page_without_a_post_is_not_a_post():
    for page in (f"<html><body><p>{BLOCK_PAGE}</p></body></html>", "<html><body>Please wait for verification</body></html>",
                 "", "<html><body><shreddit-post></shreddit-post></body></html>"):
        assert reddit.parse_html(page, SHARE, SHARE) is None


def test_reddits_block_page_is_a_block_page(monkeypatch, clean_db):
    """The bug: Jina Reader's copy of Reddit's block page passed for the post."""
    assert web._blocked(BLOCK_PAGE)
    url = "https://forum.ex.org/t/un-fil"
    _fake_web(monkeypatch, {url: (200, f"<html><body><h1>Blocked</h1><p>{BLOCK_PAGE}</p></body></html>"),
                            "https://r.jina.ai/": (200, {"data": {"content": BLOCK_PAGE, "title": "Blocked"}}),
                            "https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError, match="refuse l'accès aux serveurs"):      # never a card about a block page
        web.extract(url, url)
    assert web.blocks_servers(url)                                                 # and the phone fetches it next time


def test_the_server_alone_says_to_share_with_the_shortcut(monkeypatch, clean_db):
    monkeypatch.setattr(web, "_browser_get", lambda url: None)
    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError, match="Raccourci « Ajouter à ma KB »"):
        extractors.extract_url(SHARE)
    # a browser that gets the post, or the Wayback Machine's copy of it
    monkeypatch.setattr(web, "_browser_get", lambda url: SHREDDIT.encode())
    assert extractors.extract_url(POST).metadata["via"] == "browser"
    monkeypatch.setattr(web, "_browser_get", lambda url: b"<html>You've been blocked by network security.</html>")
    _fake_web(monkeypatch, {
        "https://archive.org/wayback/available": (200, {"archived_snapshots": {"closest": {
            "available": True, "timestamp": "20260305000000"}}}),
        f"https://web.archive.org/web/20260305000000id_/{CANONICAL}": (200, SHREDDIT)})
    ex = extractors.extract_url(POST)
    assert ex.metadata["via"] == "wayback" and ex.title == "Mes règles pour Claude après six mois"


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_shared_from_the_reddit_app_with_the_shortcut(client, monkeypatch):
    """Reddit is a site that refuses servers: the Shortcut fetches the post from the phone (following the share link)
    and the card is the post, with its author, its subreddit and its address."""
    from app import db

    def server_must_not_try(url):
        raise AssertionError("the server should read the phone's copy")

    monkeypatch.setattr(extractors, "extract_url", server_must_not_try)
    body = client.post("/api/ingest", json={"url": SHARE, "note": "utile même si un peu daté", "page_follows": "1"},
                       headers=AUTH).json()
    assert body["page_wanted"] is True and body["page_url"] == SHARE
    assert drain() == 0                                                  # waits for the phone's page
    _page_upload(client, body["id"], SHREDDIT.encode())
    drain()
    row = db.fetchone("select status, kind, title, author, site_name, source_url, content, metadata from items where id = %s",
                      (body["id"],))
    assert row["status"] == "ready" and row["kind"] == "article"
    assert row["title"] == "Mes règles pour Claude après six mois" and row["author"] == "u/alice_dev"
    assert row["site_name"] == "Reddit · r/exemple_ia" and row["source_url"] == CANONICAL
    assert "Demander un plan avant le code" in row["content"] and row["metadata"]["via"] == "phone"
    # the same post shared again by its address: already in the KB
    again = client.post("/api/ingest", json={"url": POST, "page_follows": "1"}, headers=AUTH).json()
    assert again["id"] == body["id"] and again["items"][0]["duplicate"] is True


def test_the_phone_got_a_block_page_too(client, monkeypatch):
    from app import db

    monkeypatch.setattr(web, "_browser_get", lambda url: None)
    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    item_id = client.post("/api/ingest", json={"url": SHARE, "page_follows": "1"}, headers=AUTH).json()["id"]
    _page_upload(client, item_id, b"<html><body>You've been blocked by network security.</body></html>")
    drain()
    row = db.fetchone("select status, error from items where id = %s", (item_id,))
    assert row["status"] == "error" and "page envoyée par ton téléphone est bien arrivée" in row["error"]
    assert "capture d'écran" in row["error"]


def test_network_errors_from_reddit_are_not_cards(monkeypatch, clean_db):
    monkeypatch.setattr(web, "_browser_get", lambda url: None)
    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (0, httpx.ConnectError("down"))})
    with pytest.raises(ExtractionError):
        reddit.extract(SHARE, SHARE)
