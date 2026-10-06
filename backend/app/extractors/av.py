"""Audio et vidéo : fichiers envoyés, et liens TikTok / Instagram / Vimeo / podcasts… via yt-dlp."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import llm, media
from ..config import get_settings
from .base import ExtractionError, Extracted, parse_date

log = logging.getLogger(__name__)

SHORT_VIDEO_SECONDS = 300
FEW_WORDS = 300


def ydl_opts(**extra) -> dict:
    s = get_settings()
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30, "retries": 3}
    if s.youtube_proxy_url:
        opts["proxy"] = s.youtube_proxy_url
    opts.update(extra)
    return opts


def _visual_summary(path: Path, workdir: Path, context: str) -> str | None:
    if not get_settings().anthropic_api_key:
        return None
    frames = media.extract_frames(path, workdir, count=4)
    if not frames:
        return None
    try:
        return llm.describe_frames([f.read_bytes() for f in frames], context=context)
    except Exception:
        log.warning("Description visuelle impossible", exc_info=True)
        return None


def extract_file(path: Path, filename: str | None, mime: str | None, workdir: Path) -> Extracted:
    is_video = media.has_video_stream(path)
    duration = media.probe_duration(path)
    transcript = ""
    try:
        transcript = media.transcribe_file(path, workdir)
    except Exception as exc:
        log.warning("Transcription impossible : %s", exc)
        if not is_video:
            raise
    content = transcript
    if is_video and len(transcript) < FEW_WORDS:
        visual = _visual_summary(path, workdir, context=filename or "")
        if visual:
            content = f"Ce qu'on voit dans la vidéo :\n{visual}\n\n{transcript}".strip()
    return Extracted(
        kind="video" if is_video else "audio",
        title=Path(filename).stem if filename else None,
        content=content,
        metadata={"filename": filename, "duration": duration},
    )


def extract_url(url: str, canonical: str) -> Extracted:
    from yt_dlp import YoutubeDL

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        try:
            with YoutubeDL(ydl_opts(skip_download=True)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise RuntimeError(f"yt-dlp n'a pas pu lire la page : {exc}") from exc
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(e for e in info["entries"] if e)

        if info.get("is_live") or info.get("live_status") == "is_live":
            # un direct (Twitch…) serait enregistré sans fin et bloquerait le worker
            raise ExtractionError("Direct en cours : impossible à capturer")
        duration = info.get("duration") or 0
        if duration and duration > get_settings().max_media_minutes * 60:
            raise ExtractionError(f"Média trop long ({int(duration // 60)} min)")
        is_video = info.get("vcodec") not in (None, "none") or bool(info.get("width"))
        want_frames = is_video and duration and duration <= SHORT_VIDEO_SECONDS
        # « best » exige un format audio+vidéo combiné : repli sur une fusion pour les sites en DASH séparé
        fmt = "best[height<=480]/bv*[height<=480]+ba/best/bv*+ba" if want_frames else "bestaudio/best"

        transcript, path = "", None
        with YoutubeDL(ydl_opts(format=fmt, outtmpl=str(tmpdir / "media.%(ext)s"))) as ydl:
            ydl.download([info.get("webpage_url") or url])
        files = [p for p in tmpdir.glob("media.*") if p.suffix not in (".part", ".ytdl")]
        if files:
            path = files[0]
            try:
                transcript = media.transcribe_file(path, tmpdir)
            except Exception as exc:
                log.warning("Transcription impossible : %s", exc)

        description = (info.get("description") or "").strip()
        content_parts = []
        if description:
            content_parts.append("Description :\n" + description[:4000])
        if path and want_frames and len(transcript) < FEW_WORDS:
            visual = _visual_summary(path, tmpdir, context=info.get("title") or "")
            if visual:
                content_parts.append("Ce qu'on voit dans la vidéo :\n" + visual)
        if transcript:
            content_parts.append("Transcription :\n" + transcript)

    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    published = parse_date(info.get("upload_date"))
    if not published and info.get("timestamp"):
        published = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc)
    return Extracted(
        kind="video" if is_video else "audio",
        title=info.get("title") or info.get("fulltitle"),
        content="\n\n".join(content_parts),
        source_url=canonical,
        author=uploader,
        author_url=info.get("uploader_url") or info.get("channel_url"),
        site_name=info.get("extractor_key") or info.get("extractor"),
        published_at=published,
        thumbnail_url=info.get("thumbnail"),
        metadata={"duration": duration, "extractor": info.get("extractor_key"), "view_count": info.get("view_count"),
                  "like_count": info.get("like_count")},
    )
