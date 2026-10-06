"""Where the digest agent looks: Hacker News, Hugging Face papers, GitHub, RSS/Atom feeds and followed people on X.

Every collector is best effort: a source that fails is logged (and flagged on its `watch` row) and the digest is
written with the others.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx

from .. import db
from ..config import get_settings

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; KB-digest/1.0)"

# Public feeds followed by default (the user can mute them or add others in the app).
DEFAULT_FEEDS = [
    ("Techmeme", "https://www.techmeme.com/feed.xml", "L'actualité tech générale, triée par Techmeme."),
    ("OpenAI News", "https://openai.com/news/rss.xml", "Annonces d'OpenAI."),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", "Annonces et recherche de Google DeepMind."),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "Modèles ouverts, outils, tutoriels."),
    ("Import AI (Jack Clark)", "https://importai.substack.com/feed", "Newsletter hebdo sur la recherche en IA."),
    ("Interconnects (Nathan Lambert)", "https://www.interconnects.ai/feed", "Post-training, modèles ouverts."),
    ("Ahead of AI (Sebastian Raschka)", "https://magazine.sebastianraschka.com/feed", "LLM expliqués en détail."),
    ("Simon Willison", "https://simonwillison.net/atom/everything/", "Outils LLM, essais concrets."),
    ("Lil'Log (Lilian Weng)", "https://lilianweng.github.io/index.xml", "Synthèses de recherche approfondies."),
]
GITHUB_TOPICS = ("llm", "machine-learning", "ai-agents")


@dataclass
class Candidate:
    key: str
    title: str
    url: str
    source: str                      # "Hacker News", "Hugging Face Papers", "GitHub", feed name, "X"
    kind: str                        # news | paper | repo | blog | post
    text: str = ""
    author: str | None = None
    person: str | None = None        # followed person this comes from, if any
    published_at: str | None = None  # ISO 8601
    score: float = 0.0               # popularity in its source (points, upvotes, stars), 0 if unknown
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def canonical(url: str) -> str:
    """Strip tracking parameters and fragments so the same story from two sources dedupes."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    query = "&".join(p for p in parts.query.split("&") if p and not p.lower().startswith(("utm_", "ref=", "s=")))
    host = parts.netloc.lower().removeprefix("www.").replace("twitter.com", "x.com")
    return urlunsplit((parts.scheme.lower() or "https", host, parts.path.rstrip("/"), query, ""))


def make_key(url: str) -> str:
    return hashlib.sha1(canonical(url).encode()).hexdigest()[:12]


def _clean(text: str | None, limit: int = 1200) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _http() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _get_json(url: str, params: dict | None = None, headers: dict | None = None):
    with _http() as client:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


def _get_text(url: str) -> str:
    with _http() as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _get_feed(url: str) -> tuple[bytes, dict, str]:
    """Raw bytes (feedparser finds the encoding itself), headers and final URL (to resolve relative links)."""
    with _http() as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content, dict(r.headers), str(r.url)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def hacker_news(since: datetime, min_points: int = 120, limit: int = 40) -> list[Candidate]:
    # `search` (not `search_by_date`) ranks by points: over a week, the top stories of every day make the cut
    data = _get_json("https://hn.algolia.com/api/v1/search", {
        "tags": "story",
        "numericFilters": f"created_at_i>{int(since.timestamp())},points>{min_points}",
        "hitsPerPage": 100,
    })
    hits = sorted(data.get("hits") or [], key=lambda h: h.get("points") or 0, reverse=True)[:limit]
    out = []
    for h in hits:
        discussion = f"https://news.ycombinator.com/item?id={h['objectID']}"
        url = h.get("url") or discussion
        out.append(Candidate(
            key=make_key(url), title=h.get("title") or "", url=url, source="Hacker News", kind="news",
            text=_clean(h.get("story_text")), author=h.get("author"), score=float(h.get("points") or 0),
            published_at=datetime.fromtimestamp(h.get("created_at_i") or 0, timezone.utc).isoformat(),
            extra={"discussion": discussion, "comments": h.get("num_comments")},
        ))
    return out


def hf_papers(days: list[date], limit: int = 30) -> list[Candidate]:
    seen, out = set(), []
    for d in days:
        for p in _get_json("https://huggingface.co/api/daily_papers", {"date": d.isoformat()}) or []:
            paper = p.get("paper") or {}
            pid = paper.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            url = f"https://huggingface.co/papers/{pid}"
            authors = ", ".join(a.get("name", "") for a in (paper.get("authors") or [])[:4])
            org = (paper.get("organization") or p.get("organization") or {}).get("fullname")
            out.append(Candidate(
                key=make_key(url), title=paper.get("title") or p.get("title") or "", url=url,
                source="Hugging Face Papers", kind="paper", text=_clean(paper.get("summary") or p.get("summary")),
                author=", ".join(x for x in (authors, org) if x) or None, score=float(paper.get("upvotes") or 0),
                published_at=p.get("publishedAt"), extra={"arxiv": f"https://arxiv.org/abs/{pid}"},
            ))
    return sorted(out, key=lambda c: c.score, reverse=True)[:limit]


def github_repos(since: date, limit: int = 15) -> list[Candidate]:
    token = get_settings().github_token
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    found: dict[str, Candidate] = {}
    for topic in GITHUB_TOPICS:
        data = _get_json("https://api.github.com/search/repositories", {
            "q": f"topic:{topic} created:>={since.isoformat()} stars:>=40", "sort": "stars", "order": "desc",
            "per_page": 15,
        }, headers=headers)
        for r in data.get("items") or []:
            url = r["html_url"]
            if url not in found:
                found[url] = Candidate(
                    key=make_key(url), title=r["full_name"], url=url, source="GitHub", kind="repo",
                    text=_clean(r.get("description")), author=(r.get("owner") or {}).get("login"),
                    score=float(r.get("stargazers_count") or 0), published_at=r.get("created_at"),
                    extra={"language": r.get("language"), "topics": (r.get("topics") or [])[:6]},
                )
    return sorted(found.values(), key=lambda c: c.score, reverse=True)[:limit]


def feed(name: str, feed_url: str, since: datetime, person: str | None = None, limit: int = 8) -> list[Candidate]:
    content, headers, final_url = _get_feed(feed_url)
    parsed = feedparser.parse(content, response_headers={**{k.lower(): v for k, v in headers.items()},
                                                         "content-location": final_url})
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"flux illisible : {parsed.get('bozo_exception')}")
    out = []
    for e in parsed.entries[:40]:
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        when = datetime(*stamp[:6], tzinfo=timezone.utc) if stamp else None
        if when and when < since:
            continue
        url = e.get("link")
        if not url or not e.get("title"):
            continue
        summary = e.get("summary") or (e.get("content") or [{}])[0].get("value")
        out.append(Candidate(
            key=make_key(url), title=_clean(e.get("title"), 300), url=url, source=name, person=person,
            kind="blog" if person else "news", text=_clean(summary), author=e.get("author") or person,
            published_at=when.isoformat() if when else None,
        ))
    return out[:limit]


def x_posts(handles: dict[str, str], since: datetime, budget: int) -> list[Candidate]:
    """Recent original posts of followed people (one search query per batch of accounts, capped by `budget`)."""
    token = get_settings().x_bearer_token
    if not token or budget <= 0 or not handles:
        return []
    from ..extractors.twitter import XClient, _full_text   # reuse the field fallbacks of the tweet extractor

    client = XClient(token)
    batches, current = [], []
    for handle in handles:
        clause = " OR ".join(f"from:{h}" for h in current + [handle])
        if current and len(f"({clause}) -is:retweet -is:reply") > 480:
            batches.append(current)
            current = []
        current.append(handle)
    if current:
        batches.append(current)

    out, start = [], max(since, datetime.now(timezone.utc) - timedelta(days=6, hours=23))
    for batch in batches:
        remaining = budget - client.reads
        if remaining < 10:
            break
        query = "(" + " OR ".join(f"from:{h}" for h in batch) + ") -is:retweet -is:reply"
        payload = client._get("/tweets/search/recent", {
            "query": query, "max_results": min(100, remaining), "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        client._absorb(payload)
        data = payload.get("data") or []
        for t in data:
            user = client.users.get(t.get("author_id"), {})
            handle = user.get("username") or ""
            url = f"https://x.com/{handle or 'i'}/status/{t['id']}"
            text = _full_text(t)
            metrics = t.get("public_metrics") or {}
            out.append(Candidate(
                key=make_key(url), title=_clean(text, 140), url=url, source="X", kind="post", text=_clean(text, 2000),
                author=f"{user.get('name', handle)} (@{handle})" if handle else None,
                person=handles.get(handle.lower()) or user.get("name"),
                score=float(metrics.get("like_count") or 0), published_at=t.get("created_at"),
            ))
    return out


# ---------------------------------------------------------------------------
# Watch list
# ---------------------------------------------------------------------------

def ensure_default_feeds() -> None:
    """Seed the default feeds once (muting one later is respected)."""
    row = db.fetchone("select value from kb_settings where key = 'watch_seeded'")
    if row:
        return
    for name, url, note in DEFAULT_FEEDS:
        db.execute(
            """insert into watch (kind, name, feed_url, origin, note) values ('feed', %s, %s, 'default', %s)
               on conflict do nothing""",
            (name, url, note),
        )
    db.execute("""insert into kb_settings (key, value) values ('watch_seeded', '{"done": true}')
                  on conflict (key) do nothing""")


def _mark(watch_id: int, error: str | None) -> None:
    if error:
        db.execute("update watch set last_error = %s where id = %s", (error[:300], watch_id))
    else:
        db.execute("update watch set last_ok_at = now(), last_error = null where id = %s", (watch_id,))


def collect(since: datetime, *, weekly: bool = False) -> list[Candidate]:
    """Every candidate published since `since`, deduplicated by URL. Popular-but-generic sources come first."""
    ensure_default_feeds()
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(7 if weekly else 2)]
    out: list[Candidate] = []

    working = 0

    def attempt(label: str, fn, *args, **kwargs):
        nonlocal working
        try:
            out.extend(fn(*args, **kwargs))
            working += 1
        except Exception as exc:
            log.warning("Source %s indisponible : %s", label, exc)

    attempt("Hacker News", hacker_news, since, min_points=250 if weekly else 120, limit=60 if weekly else 40)
    attempt("Hugging Face Papers", hf_papers, days, limit=40 if weekly else 25)
    attempt("GitHub", github_repos, today - timedelta(days=7), limit=20 if weekly else 12)

    watched = db.fetchall("select * from watch where status = 'active' order by kind, id")
    for w in watched:
        if not w["feed_url"]:
            continue
        try:
            out.extend(feed(w["name"], w["feed_url"], since, person=w["name"] if w["kind"] == "person" else None,
                            limit=15 if weekly else 6))
            _mark(w["id"], None)
            working += 1
        except Exception as exc:
            log.warning("Flux %s indisponible : %s", w["feed_url"], exc)
            _mark(w["id"], str(exc))

    handles = {w["x_handle"].lower(): w["name"] for w in watched if w["kind"] == "person" and w["x_handle"]}
    budget = get_settings().digest_x_max_posts * (3 if weekly else 1)
    attempt("X", x_posts, handles, since, budget)
    if not working:
        # network down or every source failing: better an error (retried within the hour) than an empty digest
        raise RuntimeError("Aucune source joignable (réseau ou sources en panne)")

    unique: dict[str, Candidate] = {}
    for c in out:
        if c.title and c.url and c.key not in unique:
            unique[c.key] = c
    return list(unique.values())


def shown_recently(days: int = 14, *, except_day: date | None = None) -> set[str]:
    """Keys of entries already shown in recent daily digests (a story isn't served twice).

    `except_day`: the daily digest being rebuilt, whose own entries don't count.
    """
    rows = db.fetchall(
        """select e->>'key' as key from digests, jsonb_array_elements(coalesce(data->'entries', '[]'::jsonb)) e
           where status = 'ready' and kind = 'daily' and created_at > now() - make_interval(days => %s)
             and period_start is distinct from %s""",
        (days, except_day),
    )
    return {r["key"] for r in rows if r["key"]}


def already_in_kb(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    canon = {canonical(u): u for u in urls}
    rows = db.fetchall("select source_url from items where source_url = any(%s)", (list(canon) + urls,))
    found = {canonical(r["source_url"]) for r in rows if r["source_url"]}
    return {u for c, u in canon.items() if c in found}


_FEED_LINK = re.compile(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.I)
_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


def discover_feed(url: str) -> str | None:
    """The RSS/Atom feed of a site: the URL itself if it's a feed, else the one its HTML advertises."""
    text = _get_text(url)
    head = text[:1000].lower()
    if "<rss" in head or "<feed" in head or "<rdf:rdf" in head:
        return url
    for tag in _FEED_LINK.findall(text[:200_000]):
        if m := _HREF.search(tag):
            return str(httpx.URL(url).join(unescape(m.group(1))))
    return None
