"""Outils audio/vidéo : ffmpeg, transcription (API compatible OpenAI), images clés."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import httpx

from . import costs
from .config import get_settings

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 600  # 10 min par segment (~2,4 Mo en MP3 32 kb/s)


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)


def probe_duration(path: Path) -> float | None:
    try:
        out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], timeout=60)
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


def has_video_stream(path: Path) -> bool:
    try:
        # « V » (majuscule) exclut les pochettes intégrées (MP3/M4A de podcasts), que « v » compte comme vidéo
        out = run(["ffprobe", "-v", "error", "-select_streams", "V", "-show_entries", "stream=codec_type",
                   "-of", "json", str(path)], timeout=60)
        return bool(json.loads(out.stdout).get("streams"))
    except Exception:
        return False


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def transcribe_file(path: Path, workdir: Path) -> str:
    """Transcrit un fichier audio/vidéo. Renvoie un texte horodaté « [m:ss] … »."""
    s = get_settings()
    if not s.transcription_api_key:
        raise RuntimeError("TRANSCRIPTION_API_KEY manquant : impossible de transcrire l'audio")

    duration = probe_duration(path)
    if duration and duration > s.max_media_minutes * 60:
        raise RuntimeError(f"Média trop long ({int(duration // 60)} min > {s.max_media_minutes} min)")

    seg_dir = workdir / "segments"
    seg_dir.mkdir(exist_ok=True)
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "32k", "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
        str(seg_dir / "part_%03d.mp3"),
    ])
    parts = sorted(seg_dir.glob("part_*.mp3"))
    lines: list[str] = []
    for i, part in enumerate(parts):
        offset = i * SEGMENT_SECONDS
        lines.extend(_transcribe_segment(part, offset))
    return "\n".join(lines).strip()


def _transcribe_segment(part: Path, offset: float) -> list[str]:
    s = get_settings()
    url = s.transcription_base_url.rstrip("/") + "/audio/transcriptions"
    headers = {"Authorization": f"Bearer {s.transcription_api_key}"}
    for fmt in ("verbose_json", "json"):
        r = _post_with_retry(url, headers, s.transcription_model, fmt, part)
        if r.status_code == 400 and fmt == "verbose_json":
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Transcription {r.status_code} : {r.text[:300]}")
        data = r.json()
        seconds = float(data.get("duration") or probe_duration(part) or SEGMENT_SECONDS)
        costs.record("transcription", costs.transcription_cost(s.transcription_model, seconds),
                     model=s.transcription_model, units={"seconds": round(seconds, 1)})
        segments = data.get("segments") or []
        if not segments:
            return [f"[{fmt_ts(offset)}] {data.get('text', '').strip()}"]
        return _group_segments(segments, offset)
    return []


def _post_with_retry(url: str, headers: dict, model: str, fmt: str, part: Path, attempts: int = 5) -> httpx.Response:
    """Les API de transcription limitent le débit (429) : on attend puis on réessaie."""
    for attempt in range(attempts):
        with part.open("rb") as f:
            r = httpx.post(url, headers=headers, data={"model": model, "response_format": fmt},
                           files={"file": (part.name, f, "audio/mpeg")}, timeout=600)
        if r.status_code != 429 and r.status_code < 500:
            return r
        try:
            wait = float(r.headers.get("retry-after", ""))
        except ValueError:
            wait = 5 * 2 ** attempt
        log.info("Transcription : %s, nouvel essai dans %.0f s", r.status_code, min(wait, 120))
        time.sleep(min(wait, 120))
    return r


def _group_segments(segments: list[dict], offset: float, every: float = 45.0) -> list[str]:
    """Regroupe les segments en paragraphes d'environ 45 s, préfixés par l'horodatage."""
    lines, buf, start = [], [], None
    for seg in segments:
        if start is None:
            start = seg.get("start", 0)
        buf.append(str(seg.get("text", "")).strip())
        if seg.get("end", 0) - start >= every:
            lines.append(f"[{fmt_ts(offset + start)}] " + " ".join(buf))
            buf, start = [], None
    if buf:
        lines.append(f"[{fmt_ts(offset + (start or 0))}] " + " ".join(buf))
    return lines


def extract_frames(path: Path, workdir: Path, count: int = 4) -> list[Path]:
    """Extrait quelques images réparties sur la vidéo."""
    duration = probe_duration(path) or 0
    if duration <= 0:
        return []
    frames = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        out = workdir / f"frame_{i}.jpg"
        try:
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                 "-vf", "scale='min(1280,iw)':-2", str(out)], timeout=120)
            if out.exists():
                frames.append(out)
        except Exception:
            log.warning("Extraction d'image impossible à t=%.1f", t)
    return frames


def parse_vtt(text: str) -> str:
    """Convertit des sous-titres WebVTT/SRT en texte horodaté sans doublons."""
    lines, last, current_ts = [], "", None
    bucket: list[str] = []
    bucket_start = None
    for raw in text.splitlines():
        raw = raw.strip()
        if "-->" in raw:
            current_ts = _vtt_seconds(raw.split("-->")[0].strip())
            continue
        if not raw or raw.isdigit() or raw.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        clean = _strip_tags(raw)
        if not clean or clean == last:
            continue
        last = clean
        if bucket_start is None:
            bucket_start = current_ts or 0
        bucket.append(clean)
        if current_ts is not None and current_ts - bucket_start >= 45:
            lines.append(f"[{fmt_ts(bucket_start)}] " + " ".join(bucket))
            bucket, bucket_start = [], None
    if bucket:
        lines.append(f"[{fmt_ts(bucket_start or 0)}] " + " ".join(bucket))
    return "\n".join(lines)


def _vtt_seconds(ts: str) -> float:
    ts = ts.replace(",", ".").split(" ")[0]
    parts = ts.split(":")
    try:
        parts_f = [float(p) for p in parts]
    except ValueError:
        return 0.0
    while len(parts_f) < 3:
        parts_f.insert(0, 0.0)
    h, m, s = parts_f
    return h * 3600 + m * 60 + s


def _strip_tags(s: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").replace("&amp;", "&").strip()
