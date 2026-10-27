"""Reddit posts: only a browser reads them, so the Shortcut opens a post shared from the Reddit app in Safari, and
shared from there it comes with the page's text. The pages below are fictional, in the shapes reddit.com,
old.reddit.com, search engines (schema.org) and Safari's share sheet give."""

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
    # a share link only redirects: kept as it is (Safari follows it)
    assert urls.classify(SHARE) == urls.UrlInfo("web", "https://reddit.com/r/exemple_ia/s/Xy12AbCd", {})
    assert urls.is_reddit(SHARE) and urls.is_reddit("redd.it/abc") and not urls.is_reddit("https://notreddit.com/x")
    # Reddit's images and videos are files the server reads like any other, not pages
    assert not urls.is_reddit("https://i.redd.it/ab12cd34ef.jpeg") and not urls.is_reddit("https://v.redd.it/ab12cd34ef")
    assert urls.reddit_post("https://v.redd.it/ab12cd34ef") == (None, None)
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


def test_the_server_alone_says_to_share_from_safari(monkeypatch, clean_db):
    """The server only tries the Wayback Machine (never as a fake browser), then says what to do."""
    def no_fake_browser(url):
        raise AssertionError("the server doesn't pose as a browser to Reddit")

    monkeypatch.setattr(web, "_browser_get", no_fake_browser)
    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    with pytest.raises(ExtractionError, match="Ouvre le post dans Safari et partage-le de là"):
        extractors.extract_url(SHARE)
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


# what Safari's share sheet gives the Shortcut for a post: the text of the page as it shows it
SAFARI_TEXT = """r/exemple_ia
alice_dev
Mes règles pour Claude après six mois
Après six mois, voici ce qui marche le mieux pour moi.
Donner un exemple de sortie attendue.
Demander un plan avant le code.
Relire le plan avant de le laisser écrire quoi que ce soit, et le corriger tout de suite s'il part ailleurs.
Le reste compte beaucoup moins. Bonne journée.
bob_ml
Le plan avant le code, c'est le meilleur conseil. Je le fais même pour des scripts de dix lignes.
dave
Je fais pareil avec des tests écrits d'abord : il voit ce qui est attendu avant de commencer.
erin_data
Merci, je garde la liste."""


def _prepare(client, **body):
    r = client.post("/api/ingest/prepare", json=body, headers=AUTH)
    assert r.status_code == 200
    return r.json()


def test_shared_from_the_reddit_app_it_opens_in_safari(client, monkeypatch):
    """The Reddit app shares a link: before asking anything, the Shortcut learns from the KB to open it in Safari
    (x-safari- opens it there whatever the default browser), and nothing is saved yet."""
    from app import db

    body = _prepare(client, url=SHARE, text=SHARE)
    assert body["open_url"] == "x-safari-" + SHARE and "Safari" in body["message"]
    assert body["choices"][0] == "Automatique" and body["choices"][-1] == "Espace Perso"
    assert _prepare(client, url="", text=f"Mes règles pour Claude\n{POST}")["open_url"] == "x-safari-" + POST
    assert _prepare(client, url="redd.it/1abcde2")["open_url"] == "x-safari-https://redd.it/1abcde2"
    # anything else: only the folders
    for other in ({"url": "https://www.lemonde.fr/x"}, {"url": "https://i.redd.it/ab12cd34ef.jpeg"}, {"text": "une idée"},
                  {}):
        assert _prepare(client, **other) == {"choices": body["choices"]}, other
    # a Shortcut from before: its share isn't saved, and it shows where to share it from
    old = client.post("/api/ingest", json={"url": SHARE, "text": SHARE, "page_follows": "1"}, headers=AUTH).json()
    assert old["ok"] is False and old["message"] == body["message"] and old["open_url"] == "x-safari-" + SHARE
    assert "page_wanted" not in old                       # the phone's fetch would only get a bot check
    assert db.fetchone("select count(*) as n from items")["n"] == 0


def test_shared_from_safari_the_page_makes_the_card(client, monkeypatch):
    """Shared from Safari, the post comes with its page's text: the card is made from it, with the post's one address
    and its subreddit, and the server doesn't even try Reddit."""
    from app import db, pipeline

    assert len(SAFARI_TEXT) > pipeline.REDDIT_PAGE_MIN          # a short post: under the 2000 characters of other sites

    def server_must_not_try(url):
        raise AssertionError("the server should read the text Safari sent")

    monkeypatch.setattr(extractors, "extract_url", server_must_not_try)
    safari_url = POST + "?share_id=Ab12&utm_source=share"
    assert "open_url" not in _prepare(client, url=safari_url, text=SAFARI_TEXT)
    body = client.post("/api/ingest", json={"url": safari_url, "text": SAFARI_TEXT, "note": "pour mes prompts",
                                             "page_follows": "1"}, headers=AUTH).json()
    assert body["message"] == "Ajouté à ta KB ✓" and "page_wanted" not in body
    drain()
    row = db.fetchone("select status, kind, site_name, source_url, content, input_text, metadata from items where id = %s",
                      (body["id"],))
    assert row["status"] == "ready" and row["kind"] == "article" and row["input_text"] is None
    assert row["site_name"] == "Reddit · r/exemple_ia" and row["source_url"] == CANONICAL
    assert "Demander un plan avant le code." in row["content"] and row["metadata"]["via"] == "phone"
    assert row["metadata"]["reddit_post"] == "1abcde2" and row["metadata"]["subreddit"] == "r/exemple_ia"
    # in the KB now: the Reddit app's link to it no longer needs Safari, the share says it's there
    assert "open_url" not in _prepare(client, url=POST, text=POST)
    again = client.post("/api/ingest", json={"url": POST, "text": POST, "page_follows": "1"}, headers=AUTH).json()
    assert again["id"] == body["id"] and again["items"][0]["duplicate"] is True


def test_a_failed_post_is_read_again_from_safari(client, monkeypatch):
    """Added from the app (no Safari): the card fails and says to share from Safari; shared from Safari, it's read."""
    from app import db

    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (200, {"archived_snapshots": {}})})
    item_id = client.post("/api/ingest", json={"url": POST}, headers=AUTH).json()["id"]
    drain()
    row = db.fetchone("select status, error from items where id = %s", (item_id,))
    assert row["status"] == "error" and "Ouvre le post dans Safari" in row["error"] and "capture d'écran" in row["error"]
    assert _prepare(client, url=POST, text=POST)["open_url"] == "x-safari-" + POST     # failed: Safari can fix it
    again = client.post("/api/ingest", json={"url": POST, "text": SAFARI_TEXT, "page_follows": "1"}, headers=AUTH).json()
    assert again["id"] == item_id and again["message"] == "Page reçue, je la relis ✓"
    drain()
    assert db.fetchone("select status from items where id = %s", (item_id,))["status"] == "ready"


def test_a_page_the_phone_sent_is_still_read(client, monkeypatch):
    """A post's page that reached /api/items/<id>/page (a Shortcut from before) is read like the server would."""
    from app import db

    item_id = client.post("/api/ingest", json={"url": SHARE}, headers=AUTH).json()["id"]
    _page_upload(client, item_id, SHREDDIT.encode())
    drain()
    row = db.fetchone("select status, title, author, source_url from items where id = %s", (item_id,))
    assert row["status"] == "ready" and row["author"] == "u/alice_dev" and row["source_url"] == CANONICAL


def test_network_errors_from_reddit_are_not_cards(monkeypatch, clean_db):
    _fake_web(monkeypatch, {"https://archive.org/wayback/available": (0, httpx.ConnectError("down"))})
    with pytest.raises(ExtractionError):
        reddit.extract(SHARE, SHARE)
