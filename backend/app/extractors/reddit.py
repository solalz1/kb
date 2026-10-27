"""Reddit posts: the post (title, text or shared link, author, subreddit, date) and its top comments.

Reddit refuses servers: a datacenter IP gets "You've been blocked by network security", its JSON endpoints are closed
to anonymous clients, its API has only given developer tokens on approval since late 2025, and a page fetched by
anything but a browser is a bot check, even from a phone. So only a browser reads a post: the Shortcut opens a link
shared from the Reddit app in Safari (pipeline.safari_link), and shared from Safari it comes with the page's text,
which makes the card (`page_base` gives it its address and subreddit). Without it, the server only tries the Wayback
Machine, then says to share the post from Safari. `parse_html` reads a post's page as a browser got it (the Wayback
Machine's copy, or a page the phone sent).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import lxml.html

from .. import urls
from . import web
from .base import ExtractionError, Extracted, parse_date

log = logging.getLogger(__name__)

MAX_COMMENTS = 12
MAX_COMMENT_CHARS = 1500
POST_TYPES = ("DiscussionForumPosting", "SocialMediaPosting")
_BLOCKS = ".//p|.//li|.//pre|.//blockquote|.//h1|.//h2|.//h3|.//h4|.//h5|.//h6"

NEEDS_SAFARI = ("Reddit ne laisse lire ses posts qu'à un navigateur. Ouvre le post dans Safari et partage-le de là avec "
                "le Raccourci : il envoie le texte de la page (partagé depuis l'app Reddit, le Raccourci l'ouvre dans "
                "Safari pour toi). Sinon, une capture d'écran du post marche aussi.")


def _clean(text: str | None) -> str:
    return re.sub(r"[ \t ]+", " ", (text or "")).strip()


def _blocks_text(el) -> str:
    """The text of a post or comment body, one paragraph or list item per line."""
    blocks = el.xpath(_BLOCKS)
    inner = {b for b in blocks for a in b.iterancestors() if a in blocks}
    lines = []
    for b in blocks:
        if b in inner:
            continue
        for br in b.iter("br"):
            br.tail = "\n" + (br.tail or "")
        text = "\n".join(_clean(line) for line in b.text_content().splitlines()).strip()
        if not text:
            continue
        if b.tag == "li":
            text = f"- {text}"
        elif b.tag == "blockquote":
            text = "> " + text.replace("\n", "\n> ")
        elif b.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = f"### {text}"
        lines.append(text)
    return "\n\n".join(lines) if lines else _clean(el.text_content())


def _name(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("name") or value.get("alternateName")
    name = _clean(str(value)) if value else ""
    name = name.removeprefix("/u/").removeprefix("u/")
    return name or None


def _json_ld(doc) -> dict:
    """The post as the page describes it for search engines (schema.org), if it does."""
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text_content())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            if (types in POST_TYPES) or (isinstance(types, list) and set(types) & set(POST_TYPES)):
                comments = []
                for c in node.get("comment") or []:
                    if isinstance(c, dict) and _clean(c.get("text")):
                        comments.append({"author": _name(c.get("author")), "text": _clean(c.get("text"))})
                shared = node.get("sharedContent")
                return {
                    "title": _clean(node.get("headline") or node.get("name")),
                    "body": (node.get("articleBody") or node.get("text") or "").strip(),
                    "author": _name(node.get("author")),
                    "published": node.get("datePublished"),
                    "permalink": node.get("url") or node.get("@id"),
                    "link": shared.get("url") if isinstance(shared, dict) else None,
                    "comments": comments,
                }
            stack.extend(node.get("@graph") or [])
    return {}


def _shreddit(doc) -> dict:
    """The post as today's reddit.com renders it: a <shreddit-post> element and <shreddit-comment>s."""
    posts = doc.xpath("//shreddit-post")
    if not posts:
        return {}
    post = posts[0]
    body = post.xpath('.//*[@slot="text-body"]')
    comments = []
    for c in doc.xpath('//shreddit-comment[@depth="0" or not(@depth)]'):
        text = c.xpath('./*[@slot="comment"] | ./div[@slot="comment"]')
        if text:
            comments.append({"author": _name(c.get("author")), "text": _blocks_text(text[0])})
    return {
        "title": _clean(post.get("post-title")),
        "body": _blocks_text(body[0]) if body else "",
        "author": _name(post.get("author")),
        "subreddit": _clean(post.get("subreddit-prefixed-name")) or (
            f"r/{post.get('subreddit-name')}" if post.get("subreddit-name") else None),
        "published": post.get("created-timestamp"),
        "permalink": post.get("permalink"),
        "link": post.get("content-href"),
        "comments": comments,
    }


def _old_reddit(doc) -> dict:
    """The post as old.reddit.com renders it."""
    things = doc.xpath('//div[contains(concat(" ", @class, " "), " thing ") and contains(@class, "link")][@data-fullname]')
    if not things:
        return {}
    post = things[0]
    title = post.xpath('.//a[contains(concat(" ", @class, " "), " title ")]')
    body = post.xpath('.//div[contains(@class, "usertext-body")]//div[contains(@class, "md")]')
    stamp = post.get("data-timestamp")
    comments = []
    for c in doc.xpath('//div[contains(@class, "commentarea")]/div[contains(@class, "sitetable")]'
                       '/div[contains(concat(" ", @class, " "), " comment ")]'):
        text = c.xpath('./div[contains(@class, "entry")]//div[contains(@class, "usertext-body")]//div[contains(@class, "md")]')
        if text:
            comments.append({"author": _name(c.get("data-author")), "text": _blocks_text(text[0])})
    return {
        "title": _clean(title[0].text_content()) if title else "",
        "body": _blocks_text(body[0]) if body else "",
        "author": _name(post.get("data-author")),
        "subreddit": _clean(post.get("data-subreddit-prefixed")) or None,
        "published": datetime.fromtimestamp(int(stamp) / 1000, timezone.utc) if stamp and stamp.isdigit() else None,
        "permalink": post.get("data-permalink"),
        "link": post.get("data-url"),
        "comments": comments,
    }


def parse_html(html: str | bytes, url: str, canonical: str) -> Extracted | None:
    """A Reddit post page (as the phone, a browser or the Wayback Machine got it) as a card's content; None when it
    holds no post (a block page, a login wall, a verification page)."""
    if isinstance(html, bytes):
        # Reddit's pages are UTF-8; lxml would read bytes without a declared charset as Latin-1
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("utf-8", errors="replace")
    if not html.strip():
        return None
    try:
        doc = lxml.html.fromstring(html)
    except ValueError:              # an XML declaration with an encoding: lxml wants the bytes then
        doc = lxml.html.fromstring(html.encode("utf-8"))
    except lxml.etree.ParserError:
        return None
    found = [d for d in (_json_ld(doc), _shreddit(doc), _old_reddit(doc)) if d]
    if not found:
        return None
    post: dict = {}
    for d in found:                         # the most precise source first, the others fill the gaps
        for key, value in d.items():
            if value and not post.get(key):
                post[key] = value
    if not post.get("title") and not post.get("body"):
        return None

    permalink = urljoin("https://www.reddit.com/", post["permalink"]) if post.get("permalink") else None
    for candidate in (permalink, url, canonical):
        post_id, sub = urls.reddit_post(candidate or "")
        if post_id:
            break
    sub = post.get("subreddit") or (f"r/{sub}" if sub else None)
    source = urls.reddit_canonical(post_id, sub.removeprefix("r/") if sub else None) if post_id else canonical
    published = parse_date(post.get("published"))
    author = post.get("author")
    link = post.get("link")
    if link and (urls.is_reddit(link) or link == permalink):
        link = None                         # a text post links to itself

    head = " · ".join(x for x in (sub, f"u/{author}" if author else None,
                                  published.date().isoformat() if published else None) if x)
    parts = [head, f"# {post.get('title') or '(sans titre)'}"]
    if post.get("body"):
        parts.append(post["body"])
    if link:
        parts.append(f"Lien partagé : {link}")
    comments = [c for c in post.get("comments") or [] if c.get("text") and c["text"] not in ("[deleted]", "[removed]")]
    if comments:
        parts.append("## Commentaires\n\n" + "\n".join(
            f"- u/{c['author'] or '?'} : {c['text'][:MAX_COMMENT_CHARS]}".replace("\n", "\n  ")
            for c in comments[:MAX_COMMENTS]))
    text = "\n\n".join(p for p in parts if p)
    if web._blocked(text):
        return None
    return Extracted(
        kind="article",
        title=post.get("title") or None,
        content=text,
        source_url=source,
        author=f"u/{author}" if author else None,
        author_url=f"https://www.reddit.com/user/{author}" if author else None,
        site_name=f"Reddit · {sub}" if sub else "Reddit",
        published_at=published,
        metadata={k: v for k, v in {"subreddit": sub, "reddit_post": post_id, "linked_url": link,
                                    "comments_read": len(comments[:MAX_COMMENTS])}.items() if v},
    )


def page_base(url: str, canonical: str) -> Extracted:
    """What the address says about a post whose page's text came from Safari: its one address and its subreddit."""
    post_id, sub = urls.reddit_post(url)
    return Extracted(kind="article", content="",
                     source_url=urls.reddit_canonical(post_id, sub) if post_id else canonical,
                     site_name=f"Reddit · r/{sub}" if sub else "Reddit",
                     metadata={k: v for k, v in {"subreddit": f"r/{sub}" if sub else None,
                                                 "reddit_post": post_id}.items() if v})


def extract(url: str, canonical: str) -> Extracted:
    """The server alone: Reddit refuses it, so only the Wayback Machine's copy of the post, if it has one (rarely for a
    recent post). Otherwise the error says to share the post from Safari."""
    archived = web._wayback(canonical if urls.reddit_post(canonical)[0] else url, canonical, parse=parse_html)
    if archived:
        return archived
    log.info("Reddit : %s illisible depuis le serveur", url)
    raise ExtractionError(NEEDS_SAFARI)
