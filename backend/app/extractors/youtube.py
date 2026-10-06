"""YouTube : métadonnées + transcription (sous-titres d'abord, audio transcrit en dernier recours)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from .. import media
from ..config import get_settings
from .av import ydl_opts
from .base import Extracted, http_client, parse_date

log = logging.getLogger(__name__)

PREFERRED_LANGS = ["fr", "en", "en-US", "en-GB", "fr-FR"]


def _meta_ytdlp(url: str) -> dict | None:
    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL(ydl_opts(skip_download=True, ignore_no_formats_error=True)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:
        log.warning("yt-dlp (métadonnées YouTube) : %s", exc)
        return None


def _meta_oembed(url: str) -> dict:
    try:
        with http_client() as c:
            r = c.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
        if r.status_code == 200:
            d = r.json()
            return {"title": d.get("title"), "uploader": d.get("author_name"), "uploader_url": d.get("author_url"),
                    "thumbnail": d.get("thumbnail_url")}
    except Exception:
        log.warning("oEmbed YouTube indisponible", exc_info=True)
    return {}


def _transcript_api(video_id: str) -> tuple[str, str] | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig

    proxy = get_settings().youtube_proxy_url
    api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy)) if proxy else YouTubeTranscriptApi()
    try:
        listing = api.list(video_id)
        try:
            transcript = listing.find_transcript(PREFERRED_LANGS)
        except Exception:
            transcript = next(iter(listing))
        fetched = transcript.fetch()
    except Exception as exc:
        log.info("youtube-transcript-api : %s", type(exc).__name__)
        return None
    lines, buf, start = [], [], None
    for snip in fetched:
        if start is None:
            start = snip.start
        buf.append(snip.text.replace("\n", " "))
        if snip.start - start >= 45:
            lines.append(f"[{media.fmt_ts(start)}] " + " ".join(buf))
            buf, start = [], None
    if buf:
        lines.append(f"[{media.fmt_ts(start or 0)}] " + " ".join(buf))
    return "\n".join(lines), transcript.language_code


def _subs_ytdlp(url: str, info: dict | None) -> str | None:
    from yt_dlp import YoutubeDL

    # YouTube liste dans automatic_captions toutes les traductions automatiques (« fr » pour une vidéo anglaise…) :
    # on vise la langue d'origine (clé « xx-orig ») et une seule langue, sinon un échec (429) sur une piste
    # traduite fait échouer tout le téléchargement.
    manual = [l for l in ((info or {}).get("subtitles") or {}) if l != "live_chat"]
    auto = list((info or {}).get("automatic_captions") or {})
    orig = next((l[: -len("-orig")] for l in auto if l.endswith("-orig")), None)
    lang = (next((l for l in PREFERRED_LANGS if l in manual), None) or orig or (manual[0] if manual else None)
            or next((l for l in PREFERRED_LANGS if l in auto), None) or "en")
    with tempfile.TemporaryDirectory() as tmp:
        opts = ydl_opts(skip_download=True, ignore_no_formats_error=True, writesubtitles=True, writeautomaticsub=True, subtitleslangs=[lang],
                        subtitlesformat="vtt", outtmpl=str(Path(tmp) / "subs.%(ext)s"))
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            log.info("Sous-titres yt-dlp : %s", exc)
            return None
        files = sorted(Path(tmp).glob("*.vtt"))
        return media.parse_vtt(files[0].read_text(errors="replace")) if files else None


def _audio_transcript(url: str) -> str | None:
    from yt_dlp import YoutubeDL

    if not get_settings().transcription_api_key:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with YoutubeDL(ydl_opts(format="bestaudio/best", outtmpl=str(Path(tmp) / "audio.%(ext)s"))) as ydl:
                ydl.download([url])
            files = [p for p in Path(tmp).glob("audio.*") if p.suffix not in (".part", ".ytdl")]
            return media.transcribe_file(files[0], Path(tmp)) if files else None
        except Exception as exc:
            log.warning("Transcription audio YouTube impossible : %s", exc)
            return None


def extract(video_id: str, canonical: str) -> Extracted:
    info = _meta_ytdlp(canonical)
    meta = info or _meta_oembed(canonical)

    duration = (info or {}).get("duration") or 0
    transcript, lang, method = None, None, None
    if res := _transcript_api(video_id):
        transcript, lang = res
        method = "youtube-transcript-api"
    if not transcript and (transcript := _subs_ytdlp(canonical, info)):
        method = "yt-dlp-subtitles"
    is_live = (info or {}).get("is_live") or (info or {}).get("live_status") == "is_live"
    if (not transcript and not is_live and duration <= get_settings().max_media_minutes * 60
            and (transcript := _audio_transcript(canonical))):
        method = "audio-transcription"

    parts = []
    if meta.get("description"):
        parts.append("Description :\n" + meta["description"][:4000])
    chapters = (info or {}).get("chapters") or []
    if chapters:
        parts.append("Chapitres :\n" + "\n".join(f"[{media.fmt_ts(c.get('start_time', 0))}] {c.get('title')}" for c in chapters))
    if transcript:
        parts.append("Transcription :\n" + transcript)

    ex = Extracted(
        kind="youtube",
        title=meta.get("title"),
        content="\n\n".join(parts),
        source_url=canonical,
        author=meta.get("uploader") or meta.get("channel"),
        author_url=meta.get("uploader_url") or meta.get("channel_url"),
        site_name="YouTube",
        published_at=parse_date(meta.get("upload_date")),
        language=lang or meta.get("language"),
        thumbnail_url=meta.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        metadata={"video_id": video_id, "duration": duration, "transcript_method": method,
                  "view_count": (info or {}).get("view_count")},
    )
    if not transcript:
        ex.metadata["hint"] = ("Transcription introuvable : YouTube bloque probablement l'IP du serveur. "
                               "Configure YOUTUBE_PROXY_URL (voir SETUP.md).")
    return ex
