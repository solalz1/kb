"""Pages web : articles (trafilatura ; Jina Reader puis la Wayback Machine quand le site refuse le serveur) et
aiguillage par type de contenu."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import trafilatura

from ..config import get_settings
from .base import ExtractionError, Extracted, http_client, parse_date

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 15 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024 * 1024
THIN = 400
BLOCKED = (401, 403, 451, 999)          # the site refuses the server (bot protection, datacenter IPs, region)
# Sites known to refuse servers: the Shortcut fetches their pages from the phone instead (see pipeline.ingest).
# Others are learned the first time they answer one of BLOCKED.
KNOWN_BLOCKERS = ("medium.com",)
BLOCKERS_SETTING = "blocked_hosts"
# what a bot check or a block page says instead of the article
BLOCK_PAGE = re.compile(r"just a moment|enable javascript and cookies|attention required|verify you are human|"
                        r"access denied|are you a robot|checking your browser", re.I)


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def blocks_servers(url: str) -> bool:
    """Whether this site refuses the server: a known one (and its subdomains), or one that already did."""
    host = _host(url)
    if any(host == h or host.endswith("." + h) for h in KNOWN_BLOCKERS):
        return True
    from .. import db

    try:
        row = db.fetchone("select value from kb_settings where key = %s", (BLOCKERS_SETTING,))
    except Exception:  # noqa: BLE001 — without the list, the server just tries first
        return False
    return bool(row) and host in (row["value"] or {})


def _remember_blocker(url: str, status: int) -> None:
    from datetime import datetime, timezone

    from .. import db

    try:
        db.execute(
            """insert into kb_settings (key, value) values (%s, %s)
               on conflict (key) do update set value = kb_settings.value || excluded.value, updated_at = now()""",
            (BLOCKERS_SETTING, db.jsonb({_host(url): {"status": status, "at": datetime.now(timezone.utc).isoformat()}})))
    except Exception:  # noqa: BLE001
        log.warning("Site bloquant non enregistré : %s", url, exc_info=True)


def extract(url: str, canonical: str) -> Extracted:
    try:
        with http_client(timeout=60) as c, c.stream("GET", url) as r:
            if r.status_code in BLOCKED:
                _remember_blocker(url, r.status_code)
                return _fallback(url, canonical, status=r.status_code)
            if r.status_code == 404:
                raise ExtractionError("Page introuvable (404)")
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            final_url = str(r.url)
            limit = MAX_HTML_BYTES if "html" in ctype or not ctype else MAX_FILE_BYTES
            data = bytearray()
            for chunk in r.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ExtractionError("Fichier trop volumineux")
            data = bytes(data)
    except httpx.HTTPError as exc:
        log.warning("Téléchargement direct impossible (%s), essai via Jina Reader et la Wayback Machine", exc)
        return _fallback(url, canonical, reason=str(exc))

    filename = Path(urlsplit(final_url).path).name or "fichier"
    if ctype == "application/pdf" or data[:5] == b"%PDF-":
        from . import pdf

        return pdf.extract_bytes(data, filename, source_url=canonical)
    if ctype.startswith("image/"):
        from . import image

        return image.extract_bytes(data, ctype, filename, source_url=canonical)
    if ctype.startswith(("audio/", "video/")):
        from . import av

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (filename or "media")
            path.write_bytes(data)
            ex = av.extract_file(path, filename, ctype, Path(tmp))
        ex.source_url = canonical
        return ex
    if "html" in ctype or not ctype or ctype in ("application/xhtml+xml",):
        return extract_html(data, final_url, canonical)
    if ctype.startswith("text/"):
        return Extracted(kind="article", content=data.decode("utf-8", errors="replace"), source_url=canonical,
                         title=filename, site_name=urlsplit(canonical).netloc)
    from . import document

    ex = document.extract_bytes(data, filename, ctype)
    ex.source_url = canonical
    return ex


def extract_html(html: str | bytes, url: str, canonical: str, *, jina: bool = True) -> Extracted:
    """trafilatura accepte aussi des bytes et détecte l'encodage lui-même. `jina` : relire une page trop maigre via
    Jina Reader."""
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_tables=True, include_comments=False)
    text = trafilatura.extract(
        html, url=url, output_format="markdown", include_tables=True, include_comments=False, include_links=False
    ) or ""
    title = getattr(doc, "title", None) if doc else None
    description = getattr(doc, "description", None) if doc else None
    host = urlsplit(canonical).netloc

    if len(text) < THIN and jina:
        read = _jina(url)
        if read and len(read.get("content", "")) > len(text) and not _blocked(read["content"]):
            text = read["content"]
            title = title or read.get("title")
    if len(text) < THIN and description and description not in text:
        text = (text + "\n\n" + description).strip()

    ex = Extracted(
        kind="article",
        title=title,
        content=text,
        source_url=canonical,
        author=getattr(doc, "author", None) if doc else None,
        site_name=(getattr(doc, "sitename", None) if doc else None) or host,
        published_at=parse_date(getattr(doc, "date", None) if doc else None),
        language=getattr(doc, "language", None) if doc else None,
        thumbnail_url=getattr(doc, "image", None) if doc else None,
        metadata={"description": description} if description else {},
    )
    if len(text) < THIN:
        ex.metadata["thin_content"] = True
        if any(h in host for h in ("linkedin.com", "instagram.com", "facebook.com")):
            ex.metadata["hint"] = "Contenu derrière un login : partage plutôt une capture d'écran."
    return ex


def _jina(url: str) -> dict | None:
    s = get_settings()
    headers = {"Accept": "application/json", "X-Return-Format": "markdown"}
    if s.jina_api_key:
        headers["Authorization"] = f"Bearer {s.jina_api_key}"
    try:
        with http_client(headers=headers, timeout=60) as c:
            r = c.get(f"https://r.jina.ai/{url}")
        if r.status_code >= 400:
            return None
        data = r.json().get("data") or {}
        return {"content": data.get("content") or "", "title": data.get("title")}
    except Exception:
        log.warning("Jina Reader indisponible", exc_info=True)
        return None


def _blocked(text: str) -> bool:
    """A bot check or a block page rather than the article."""
    return len(text) < 3000 and bool(BLOCK_PAGE.search(text))


def _wayback(url: str, canonical: str) -> Extracted | None:
    """The latest copy of the page in the Internet Archive's Wayback Machine, if it has one."""
    try:
        with http_client(timeout=30) as c:
            r = c.get("https://archive.org/wayback/available", params={"url": url})
            snap = ((r.json().get("archived_snapshots") or {}).get("closest") or {}) if r.status_code == 200 else {}
            if not snap.get("available") or not snap.get("timestamp"):
                return None
            page = c.get(f"https://web.archive.org/web/{snap['timestamp']}id_/{url}")   # id_ : the page as archived
        if page.status_code >= 400:
            return None
        ex = extract_html(page.content, url, canonical, jina=False)
    except Exception:
        log.warning("Wayback Machine indisponible", exc_info=True)
        return None
    if ex.metadata.get("thin_content") or _blocked(ex.content):
        return None
    ex.metadata.update({"via": "wayback", "archived_at": snap["timestamp"]})
    return ex


BROWSER_PROFILES = ("chrome", "safari")      # curl_cffi impersonation targets, tried in order


def _browser_get(url: str) -> bytes | None:
    """The page fetched with a browser's TLS and HTTP/2 fingerprint (curl_cffi). Bot protections such as Cloudflare
    often refuse Python's own fingerprint with a 403 while serving the same page to a browser."""
    try:
        from curl_cffi import requests as browser
    except ImportError:
        return None
    for profile in BROWSER_PROFILES:
        try:
            r = browser.get(url, impersonate=profile, timeout=30, allow_redirects=True,
                            headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
        except Exception:  # noqa: BLE001
            log.warning("Lecture « navigateur » (%s) impossible : %s", profile, url, exc_info=True)
            continue
        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code == 200 and ("html" in ctype or not ctype):
            return r.content
        log.info("Lecture « navigateur » (%s) : %s pour %s", profile, r.status_code, url)
    return None


def _as_browser(url: str, canonical: str) -> Extracted | None:
    html = _browser_get(url)
    if not html:
        return None
    ex = extract_html(html, url, canonical, jina=False)
    if ex.metadata.get("thin_content") or _blocked(ex.content):
        return None
    ex.metadata["via"] = "browser"
    return ex


def _fallback(url: str, canonical: str, *, status: int | None = None, reason: str = "") -> Extracted:
    """The server couldn't read the page itself: again as a browser, then Jina Reader, then the Wayback Machine."""
    if status:
        as_browser = _as_browser(url, canonical)
        if as_browser:
            return as_browser
    read = _jina(url)
    if read and len(read["content"]) > 100 and not _blocked(read["content"]):
        return Extracted(kind="article", title=read.get("title"), content=read["content"], source_url=canonical,
                         site_name=urlsplit(canonical).netloc, metadata={"via": "jina"})
    archived = _wayback(url, canonical)
    if archived:
        return archived
    host = urlsplit(canonical).netloc
    if status:
        raise ExtractionError(
            f"{host} refuse l'accès aux serveurs ({status}), même lu comme un navigateur, et ni Jina Reader ni les "
            "archives du web n'ont la page. "
            "Partage-la de nouveau avec le Raccourci : il la lira depuis ton téléphone. "
            "Ou colle le lien suivi du texte de l'article dans Ajouter.")
    raise RuntimeError(f"Page inaccessible : {reason}")
