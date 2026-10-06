"""Tweets et threads via l'API officielle X v2 (FxTwitter en secours pour les Articles X)."""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .. import llm, media
from ..config import get_settings
from .base import ExtractionError, Extracted, http_client, parse_date

log = logging.getLogger(__name__)

API = "https://api.x.com/2"
MAX_THREAD = 40
MAX_IMAGES = 4

_FIELDS = "created_at,author_id,conversation_id,referenced_tweets,entities,attachments,public_metrics,note_tweet,lang"
_EXPANSIONS = "author_id,attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id"
_MEDIA = "type,url,preview_image_url,alt_text,variants,duration_ms,width,height"
_USER = "name,username,profile_image_url,verified,description"

# Jeux de paramètres essayés dans l'ordre (l'API X rejette les champs inconnus)
PARAM_SETS = [
    {"tweet.fields": _FIELDS + ",article", "expansions": _EXPANSIONS, "media.fields": _MEDIA, "user.fields": _USER},
    {"tweet.fields": _FIELDS, "expansions": _EXPANSIONS, "media.fields": _MEDIA, "user.fields": _USER},
    {
        # author_id / referenced_posts ne sont pas des valeurs admises de post.fields : les expansions les renvoient
        "post.fields": _FIELDS.replace("author_id,", "").replace("referenced_tweets,", "").replace("note_tweet", "note_post")
        + ",article",
        "expansions": "author_id,attachments.media_keys,referenced_posts",
        "media.fields": _MEDIA,
        "user.fields": _USER,
    },
]


class XClient:
    def __init__(self, token: str):
        self.http = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30)
        self.params_idx = 0
        self.tweets: dict[str, dict] = {}
        self.users: dict[str, dict] = {}
        self.media: dict[str, dict] = {}
        self.reads = 0

    def _get(self, path: str, params: dict) -> dict:
        while True:
            full = {**params, **PARAM_SETS[self.params_idx]}
            r = self.http.get(f"{API}{path}", params=full)
            if r.status_code == 400 and self.params_idx < len(PARAM_SETS) - 1:
                log.info("X API : jeu de champs %d refusé, essai suivant", self.params_idx)
                self.params_idx += 1
                continue
            if r.status_code == 401:
                raise ExtractionError("X_BEARER_TOKEN invalide (401)")
            if r.status_code == 402:
                raise ExtractionError("Crédits X API épuisés (402) : recharge ton compte développeur")
            if r.status_code == 429:
                raise RuntimeError("X API : limite de débit atteinte (429), nouvel essai plus tard")
            r.raise_for_status()
            return r.json()

    def _absorb(self, payload: dict) -> None:
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = [data]
        includes = payload.get("includes") or {}
        for t in data + (includes.get("tweets") or includes.get("posts") or []):
            self.tweets[t["id"]] = t
        for u in includes.get("users") or []:
            self.users[u["id"]] = u
        for m in includes.get("media") or []:
            self.media[m["media_key"]] = m
        self.reads += len(data)

    def lookup(self, ids: list[str]) -> None:
        ids = [i for i in ids if i not in self.tweets]
        if ids:
            self._absorb(self._get("/tweets", {"ids": ",".join(ids[:100])}))

    def search_conversation(self, conversation_id: str, username: str) -> None:
        payload = self._get(
            "/tweets/search/recent",
            {"query": f"conversation_id:{conversation_id} from:{username} -is:retweet", "max_results": 100},
        )
        self._absorb(payload)


# ---------------------------------------------------------------------------

def _refs(t: dict) -> list[dict]:
    return t.get("referenced_tweets") or t.get("referenced_posts") or []


def _ref(t: dict, kind: str) -> str | None:
    return next((r["id"] for r in _refs(t) if r.get("type") == kind), None)


def _full_text(t: dict) -> str:
    note = t.get("note_tweet") or t.get("note_post") or {}
    text = note.get("text") or t.get("text") or ""
    entities = note.get("entities") or t.get("entities") or {}
    for u in entities.get("urls") or []:
        short, expanded = u.get("url"), u.get("expanded_url") or u.get("unwound_url")
        if not short:
            continue
        if u.get("media_key") or "/photo/" in (expanded or "") or "/video/" in (expanded or ""):
            text = text.replace(short, "")
        elif expanded:
            text = text.replace(short, expanded)
    return text.strip()


def _article_text(t: dict) -> tuple[str | None, str | None]:
    art = t.get("article")
    if not isinstance(art, dict):
        return None, t.get("article_title")
    title = art.get("title") or t.get("article_title")
    for key in ("plain_text", "text", "content", "body", "preview_text"):
        val = art.get(key)
        if isinstance(val, str) and len(val) > 200:
            return val, title
        if isinstance(val, dict) and val.get("blocks"):
            return _blocks_to_md(val["blocks"]), title
    return None, title


def _blocks_to_md(blocks: list[dict]) -> str:
    out = []
    for b in blocks:
        text = (b.get("text") or "").strip()
        kind = b.get("type", "unstyled")
        if not text:
            continue
        prefix = {
            "header-one": "# ", "header-two": "## ", "header-three": "### ",
            "unordered-list-item": "- ", "ordered-list-item": "1. ", "blockquote": "> ",
        }.get(kind, "")
        out.append(prefix + text)
    return "\n\n".join(out)


def _looks_like_article_link(text: str) -> bool:
    return bool(re.fullmatch(r"\s*https?://(x|twitter)\.com/i/article/\d+\s*", text or "")) or bool(
        re.fullmatch(r"\s*https://t\.co/\w+\s*", text or "")
    )


# ---------------------------------------------------------------------------

def extract(tweet_id: str, user_hint: str | None = None) -> Extracted:
    s = get_settings()
    if not s.x_bearer_token:
        log.warning("X_BEARER_TOKEN absent : utilisation de FxTwitter")
        return _extract_fx(tweet_id)

    x = XClient(s.x_bearer_token)
    x.lookup([tweet_id])
    main = x.tweets.get(tweet_id)
    if not main:
        raise ExtractionError("Tweet introuvable (supprimé, privé ou compte protégé)")

    author_id = main.get("author_id")
    author = x.users.get(author_id, {})
    username = author.get("username") or user_hint or "i"

    # 1) remonter le thread (chaque tweet pointe vers celui auquel il répond)
    chain = [main]
    reply_context = None
    cur = main
    for _ in range(MAX_THREAD):
        parent_id = _ref(cur, "replied_to")
        if not parent_id:
            break
        if parent_id not in x.tweets:
            x.lookup([parent_id])
        parent = x.tweets.get(parent_id)
        if not parent:
            break
        if parent.get("author_id") != author_id:
            reply_context = parent
            break
        chain.insert(0, parent)
        cur = parent

    # 2) suite du thread : possible seulement si la conversation a moins de 7 jours
    created = parse_date(main.get("created_at"))
    conv_id = main.get("conversation_id")
    thread_complete = True
    if conv_id and username != "i":
        recent = created and created > datetime.now(timezone.utc) - timedelta(days=6, hours=20)
        if recent:
            try:
                x.search_conversation(conv_id, username)
                ids_in_chain = {t["id"] for t in chain}
                followers = sorted(
                    (t for t in x.tweets.values()
                     if t.get("conversation_id") == conv_id and t.get("author_id") == author_id and t["id"] not in ids_in_chain),
                    key=lambda t: t.get("created_at", ""),
                )
                for t in followers:
                    if _ref(t, "replied_to") in ids_in_chain:
                        chain.append(t)
                        ids_in_chain.add(t["id"])
            except ExtractionError:
                raise
            except Exception:
                log.warning("Recherche de la suite du thread impossible", exc_info=True)
        elif chain[0].get("id") == conv_id and len(chain) == 1:
            thread_complete = False

    # 3) texte
    parts = []
    for i, t in enumerate(chain, 1):
        prefix = f"({i}/{len(chain)}) " if len(chain) > 1 else ""
        parts.append(prefix + _full_text(t))
    content = "\n\n".join(parts)

    article_text, article_title = _article_text(main)
    if not article_text and s.x_article_fallback_fxtwitter and (main.get("article") or _looks_like_article_link(main.get("text", ""))):
        try:
            fx = _fx_status(tweet_id)
            if fx.get("article"):
                article_title = article_title or fx["article"].get("title")
                article_text = _blocks_to_md(((fx["article"].get("content") or {}).get("blocks")) or [])
        except Exception:
            log.warning("Secours FxTwitter pour l'Article X impossible", exc_info=True)
    if article_text:
        content = (f"# {article_title}\n\n" if article_title else "") + article_text

    header = f"@{username} ({author.get('name', '')})"
    if reply_context:
        ru = x.users.get(reply_context.get("author_id"), {})
        content = f"En réponse à @{ru.get('username', '?')} : « {_full_text(reply_context)[:600]} »\n\n" + content

    quoted_id = _ref(main, "quoted")
    quoted = x.tweets.get(quoted_id) if quoted_id else None
    quoted_meta = None
    if quoted:
        qu = x.users.get(quoted.get("author_id"), {})
        q_url = f"https://x.com/{qu.get('username', 'i')}/status/{quoted['id']}"
        content += f"\n\n> Tweet cité — @{qu.get('username', '?')} : {_full_text(quoted)}\n> {q_url}"
        quoted_meta = {"url": q_url, "author": qu.get("username"), "text": _full_text(quoted)[:1000]}

    # 4) médias (les tweets reçus via « includes » n'ont pas leurs médias développés : on les relit)
    missing = [t["id"] for t in chain + ([quoted] if quoted else [])
               if any(k not in x.media for k in ((t.get("attachments") or {}).get("media_keys") or []))]
    if missing:
        try:
            x._absorb(x._get("/tweets", {"ids": ",".join(missing[:100])}))
        except Exception:
            log.warning("Médias des tweets du thread indisponibles", exc_info=True)
    media_items = []
    for t in chain + ([quoted] if quoted else []):
        for key in ((t.get("attachments") or {}).get("media_keys") or []):
            if key in x.media:
                media_items.append(x.media[key])
    content_media, thumb = _process_media(media_items, header)
    if content_media:
        content += "\n\n" + content_media

    canonical = f"https://x.com/{username}/status/{tweet_id}"
    metrics = main.get("public_metrics") or {}
    return Extracted(
        kind="tweet",
        title=article_title,
        content=f"{header}\n\n{content}".strip(),
        source_url=canonical,
        author=f"{author.get('name', '')} (@{username})".strip(),
        author_url=f"https://x.com/{username}",
        site_name="X",
        published_at=created,
        language=main.get("lang"),
        thumbnail_url=thumb or author.get("profile_image_url"),
        metadata={
            "tweet_id": tweet_id,
            "conversation_id": conv_id,
            "author_username": username,
            "thread": [
                {"id": t["id"], "url": f"https://x.com/{username}/status/{t['id']}", "created_at": t.get("created_at")}
                for t in chain
            ],
            "thread_maybe_incomplete": not thread_complete,
            "metrics": metrics,
            "quoted": quoted_meta,
            "is_article": bool(article_text),
            "api": "x",
            "x_reads": x.reads,
        },
    )


def _process_media(items: list[dict], context: str) -> tuple[str, str | None]:
    lines, thumb, n_img = [], None, 0
    video_done = False
    for m in items:
        mtype = m.get("type")
        if mtype == "photo" and m.get("url"):
            thumb = thumb or m["url"]
            if n_img >= MAX_IMAGES:
                continue
            n_img += 1
            try:
                with http_client() as c:
                    data = c.get(m["url"] + "?name=large").content
                d = llm.describe_image(data, "image/jpeg", context=f"Image jointe à un tweet de {context}")
                text = f"\n   Texte : {d['text_in_image']}" if d.get("text_in_image") else ""
                lines.append(f"[Image {n_img}] {d['description']}{text}")
            except Exception:
                log.warning("Description d'image impossible", exc_info=True)
                if m.get("alt_text"):
                    lines.append(f"[Image {n_img}] {m['alt_text']}")
        elif mtype in ("video", "animated_gif"):
            thumb = thumb or m.get("preview_image_url")
            if video_done or mtype == "animated_gif":
                continue
            variants = [v for v in (m.get("variants") or []) if v.get("content_type") == "video/mp4"]
            if not variants:
                continue
            video_done = True
            url = sorted(variants, key=lambda v: v.get("bit_rate", 0))[0]["url"]
            transcript = _transcribe_url(url)
            if transcript:
                lines.append(f"[Vidéo — transcription]\n{transcript}")
    return "\n\n".join(lines), thumb


def _transcribe_url(url: str) -> str | None:
    if not get_settings().transcription_api_key:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            with http_client(timeout=300) as c, c.stream("GET", url) as r:
                r.raise_for_status()
                with path.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            dur = media.probe_duration(path) or 0
            if dur < 8:
                return None
            return media.transcribe_file(path, Path(tmp))
    except Exception:
        log.warning("Transcription de la vidéo du tweet impossible", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Secours : FxTwitter (sans clé)
# ---------------------------------------------------------------------------

def _fx_status(tweet_id: str) -> dict:
    with http_client(headers={"User-Agent": "kb-personal/1.0"}) as c:
        r = c.get(f"https://api.fxtwitter.com/i/status/{tweet_id}")
    if r.status_code == 404:
        raise ExtractionError("Tweet introuvable")
    r.raise_for_status()
    return r.json().get("tweet") or {}


def _extract_fx(tweet_id: str) -> Extracted:
    t = _fx_status(tweet_id)
    chain = [t]
    cur = t
    for _ in range(MAX_THREAD):
        parent_id = cur.get("replying_to_status")
        if not parent_id or (cur.get("replying_to") or "").lower() != t["author"]["screen_name"].lower():
            break
        try:
            cur = _fx_status(parent_id)
        except Exception:
            break
        chain.insert(0, cur)
    username = t["author"]["screen_name"]
    text = "\n\n".join(
        (f"({i}/{len(chain)}) " if len(chain) > 1 else "") + (x.get("text") or "") for i, x in enumerate(chain, 1)
    )
    if t.get("article"):
        art = t["article"]
        text = f"# {art.get('title', '')}\n\n" + _blocks_to_md(((art.get("content") or {}).get("blocks")) or [])
    if t.get("quote"):
        q = t["quote"]
        text += f"\n\n> Tweet cité — @{q['author']['screen_name']} : {q.get('text', '')}\n> {q.get('url', '')}"
    photos = ((t.get("media") or {}).get("photos")) or []
    media_lines = []
    for i, p in enumerate(photos[:MAX_IMAGES], 1):
        try:
            with http_client() as c:
                d = llm.describe_image(c.get(p["url"]).content, "image/jpeg")
            media_lines.append(f"[Image {i}] {d['description']}" + (f"\n   Texte : {d['text_in_image']}" if d.get("text_in_image") else ""))
        except Exception:
            log.warning("Description d'image impossible", exc_info=True)
    if media_lines:
        text += "\n\n" + "\n\n".join(media_lines)
    return Extracted(
        kind="tweet",
        title=(t.get("article") or {}).get("title"),
        content=f"@{username} ({t['author'].get('name', '')})\n\n{text}",
        source_url=f"https://x.com/{username}/status/{tweet_id}",
        author=f"{t['author'].get('name', '')} (@{username})",
        author_url=f"https://x.com/{username}",
        site_name="X",
        published_at=parse_date(t.get("created_at")) or (
            datetime.fromtimestamp(t["created_timestamp"], tz=timezone.utc) if t.get("created_timestamp") else None
        ),
        language=t.get("lang"),
        thumbnail_url=(photos[0]["url"] if photos else t["author"].get("avatar_url")),
        metadata={
            "tweet_id": tweet_id,
            "author_username": username,
            "thread": [{"id": x["id"], "url": x.get("url")} for x in chain],
            "metrics": {"like_count": t.get("likes"), "repost_count": t.get("retweets"), "reply_count": t.get("replies"),
                        "impression_count": t.get("views")},
            "api": "fxtwitter",
        },
    )
