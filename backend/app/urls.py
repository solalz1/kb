"""Reconnaissance et normalisation des URL partagées."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

TWEET_RE = re.compile(
    r"^(?:https?://)?(?:www\.|mobile\.|m\.)?"
    r"(?:twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|fixupx\.com|fixvx\.com|nitter\.net)"
    r"/(?:#!/)?(?:(?P<user>\w{1,15})|i(?:/web)?)/status(?:es)?/(?P<id>\d+)",
    re.IGNORECASE,
)
YOUTUBE_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"(?P<id>[A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
ARXIV_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:arxiv\.org|export\.arxiv\.org)/(?:abs|pdf|html)/"
    r"(?P<id>(?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?",
    re.IGNORECASE,
)
GITHUB_REPO_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/[^?#]*)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
# Reddit: a post's id after /comments/ (also in a comment's link), or a redd.it short link. Share links from the app
# (/r/<sub>/s/<code>) only redirect to the post: Safari follows them (pipeline.safari_link).
REDDIT_POST_RE = re.compile(r"^/(?:r/(?P<sub>[\w-]+)/)?comments/(?P<id>[a-z0-9]{4,12})(?:/|$)", re.IGNORECASE)
REDDIT_SHORT_RE = re.compile(r"^/(?P<id>[a-z0-9]{4,12})/?$", re.IGNORECASE)
GITHUB_RESERVED = {"orgs", "topics", "collections", "trending", "marketplace", "settings", "sponsors", "features", "about"}

# Plateformes dont le contenu principal est audio/vidéo (traitées via yt-dlp)
MEDIA_HOSTS = (
    "tiktok.com", "instagram.com", "vimeo.com", "dailymotion.com", "twitch.tv",
    "podcasts.apple.com", "soundcloud.com", "facebook.com", "fb.watch", "loom.com",
    "rumble.com", "bilibili.com", "ted.com", "threads.net/@",
)
MEDIA_PATH_HINTS = {
    "instagram.com": ("/reel/", "/reels/", "/p/", "/tv/"),
    "facebook.com": ("/watch", "/reel/", "/videos/"),
    "ted.com": ("/talks/",),
}

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "igsh", "mc_cid", "mc_eid", "ref", "ref_src",
    "ref_url", "source", "s", "si", "feature", "share", "share_id", "_hsenc", "_hsmi", "mkt_tok",
    "trk", "trackingid", "lipi", "spm", "cmpid", "xtor",
}


@dataclass
class UrlInfo:
    kind: str                 # tweet | youtube | paper | repo | media | web
    canonical: str
    ids: dict


def find_urls(text: str | None) -> list[str]:
    if not text:
        return []
    return [u.rstrip(".,;:!?)]}»") for u in URL_RE.findall(text)]


def only_url(text: str | None) -> str | None:
    """Renvoie l'URL si le texte n'est (presque) qu'une URL."""
    if not text:
        return None
    stripped = text.strip()
    urls = find_urls(stripped)
    if len(urls) == 1 and len(stripped) <= len(urls[0]) + 3:
        return urls[0]
    return None


def clean_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower()
    if netloc.startswith("www.") and not netloc.startswith("www.youtube."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit(("https" if parts.scheme in ("http", "https") else parts.scheme, netloc, path, urlencode(query), ""))


def is_reddit(url: str | None) -> bool:
    """A Reddit page: reddit.com and its subdomains, or a redd.it short link. Not the files on i.redd.it or
    v.redd.it, which the server reads like any image or video."""
    host = urlsplit(url if re.match(r"^https?://", url or "", re.I) else f"https://{url or ''}").netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host == "redd.it" or host == "reddit.com" or host.endswith(".reddit.com")


def reddit_post(url: str) -> tuple[str | None, str | None]:
    """(post id, subreddit) of a Reddit link to a post or one of its comments; (None, None) for anything else."""
    parts = urlsplit(url if re.match(r"^https?://", url, re.I) else f"https://{url}")
    host = parts.netloc.lower().split(":")[0]
    if host == "redd.it":
        m = REDDIT_SHORT_RE.match(parts.path)
        return (m.group("id").lower(), None) if m else (None, None)
    m = REDDIT_POST_RE.match(parts.path)
    return (m.group("id").lower(), m.group("sub")) if m else (None, None)


def reddit_canonical(post_id: str, sub: str | None = None) -> str:
    return f"https://www.reddit.com/r/{sub}/comments/{post_id}" if sub else f"https://www.reddit.com/comments/{post_id}"


def classify(url: str) -> UrlInfo:
    url = url.strip()
    if m := TWEET_RE.match(url):
        user = m.group("user") or "i"
        return UrlInfo("tweet", f"https://x.com/{user}/status/{m.group('id')}", {"tweet_id": m.group("id"), "user": user})
    if m := YOUTUBE_RE.match(url):
        return UrlInfo("youtube", f"https://www.youtube.com/watch?v={m.group('id')}", {"video_id": m.group("id")})
    if m := ARXIV_RE.match(url):
        return UrlInfo("paper", f"https://arxiv.org/abs/{m.group('id')}", {"arxiv_id": m.group("id")})
    if (m := GITHUB_REPO_RE.match(url)) and m.group("owner").lower() not in GITHUB_RESERVED:
        owner, repo = m.group("owner"), m.group("repo")
        return UrlInfo("repo", f"https://github.com/{owner}/{repo}", {"owner": owner, "repo": repo})

    if is_reddit(url):
        post_id, sub = reddit_post(url)
        if post_id:      # old., new., m., np. and www. alike, a comment's link, the slug or not: one address per post
            return UrlInfo("web", reddit_canonical(post_id, sub), {"reddit_post": post_id})

    canonical = clean_url(url)
    host = urlsplit(canonical).netloc
    path = urlsplit(canonical).path
    for media_host in MEDIA_HOSTS:
        h, _, p = media_host.partition("/")
        if host == h or host.endswith("." + h):
            hints = MEDIA_PATH_HINTS.get(h)
            if (not hints or any(x in path for x in hints)) and (not p or path.startswith("/" + p)):
                return UrlInfo("media", canonical, {})
    return UrlInfo("web", canonical, {})


def dedupe_filter(info: UrlInfo) -> tuple[str, list]:
    """Clause SQL pour retrouver un item déjà capturé pour cette URL."""
    if info.kind == "tweet":
        return "source_url like %s", [f"%/status/{info.ids['tweet_id']}"]
    if info.ids.get("reddit_post"):     # with or without its subreddit in the address
        return "source_url like %s", [f"%reddit.com/%comments/{info.ids['reddit_post']}"]
    return "source_url = %s", [info.canonical]
