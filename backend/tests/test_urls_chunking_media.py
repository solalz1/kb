import subprocess
from pathlib import Path

import pytest

from app import chunking, media, urls
from app.pipeline import _primary_url


@pytest.mark.parametrize("url,kind,canonical", [
    ("https://x.com/karpathy/status/1886192184808149383?s=46&t=abc", "tweet", "https://x.com/karpathy/status/1886192184808149383"),
    ("https://twitter.com/karpathy/status/123", "tweet", "https://x.com/karpathy/status/123"),
    ("https://mobile.twitter.com/i/web/status/456", "tweet", "https://x.com/i/status/456"),
    ("https://x.com/i/status/789", "tweet", "https://x.com/i/status/789"),
    ("https://youtu.be/dQw4w9WgXcQ?si=xyz", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/abcdefghijk", "youtube", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("https://arxiv.org/pdf/2401.12345v2", "paper", "https://arxiv.org/abs/2401.12345"),
    ("https://github.com/anthropics/anthropic-sdk-python/tree/main/src", "repo", "https://github.com/anthropics/anthropic-sdk-python"),
    ("https://www.tiktok.com/@user/video/123?is_from_webapp=1", "media", None),
    ("https://www.instagram.com/reel/C123abc/?igsh=xyz", "media", "https://instagram.com/reel/C123abc"),
    ("https://www.instagram.com/someprofile/", "web", None),
    ("https://blog.example.com/post/?utm_source=x&id=3", "web", "https://blog.example.com/post?id=3"),
])
def test_classify(url, kind, canonical):
    info = urls.classify(url)
    assert info.kind == kind
    if canonical:
        assert info.canonical == canonical


def test_only_and_primary_url():
    assert urls.only_url("  https://x.com/a/status/1 ") == "https://x.com/a/status/1"
    assert urls.only_url("regarde https://x.com/a/status/1 c'est fou, vraiment très intéressant") is None
    assert _primary_url("Check out this video! https://www.tiktok.com/@a/video/1") == "https://www.tiktok.com/@a/video/1"
    assert _primary_url("Une longue note " * 30 + " https://example.com") is None


@pytest.mark.parametrize("text,expected", [
    ("https://en.wikipedia.org/wiki/Rust_(programming_language)", "https://en.wikipedia.org/wiki/Rust_(programming_language)"),
    ("lu ici (https://en.wikipedia.org/wiki/Rust_(programming_language)).", "https://en.wikipedia.org/wiki/Rust_(programming_language)"),
    ("(voir https://example.com/post)", "https://example.com/post"),
    ("[lien](https://example.com/a), puis la suite", "https://example.com/a"),
    ("https://example.com/a?x=[1]", "https://example.com/a?x=[1]"),
    ("Regarde https://example.com/page!!", "https://example.com/page"),
    ("«https://example.com/fr»", "https://example.com/fr"),
])
def test_find_urls_trailing_punctuation(text, expected):
    assert urls.find_urls(text) == [expected]


def test_only_url_keeps_balanced_parentheses():
    url = "https://en.wikipedia.org/wiki/Rust_(programming_language)"
    assert urls.only_url(url) == url


def test_chunking_sizes_and_overlap():
    text = "\n\n".join(f"Paragraphe {i}. " + "Phrase de test assez longue pour remplir. " * 12 for i in range(30))
    chunks = chunking.chunk_text(text)
    assert len(chunks) > 5
    assert all(len(c) <= chunking.TARGET + chunking.OVERLAP + 50 for c in chunks)
    # chevauchement : la fin d'un chunk se retrouve au début du suivant
    assert chunks[0][-50:].split()[-1] in chunks[1][:400]


def test_chunking_huge_paragraph_and_empty():
    assert chunking.chunk_text("") == []
    chunks = chunking.chunk_text("mot " * 5000)
    assert len(chunks) >= 10


def test_vtt_and_timestamps():
    vtt = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000
Bonjour <c>à tous</c>

00:00:03.000 --> 00:00:05.000
Bonjour à tous

00:00:50.000 --> 00:00:52.000
On parle d'agents.
"""
    out = media.parse_vtt(vtt)
    assert out.startswith("[0:01] Bonjour à tous")
    assert "Bonjour à tous Bonjour" not in out
    assert "agents" in out
    assert media.fmt_ts(3725) == "1:02:05"
    segs = [{"start": 0, "end": 20, "text": "a"}, {"start": 20, "end": 50, "text": "b"}, {"start": 50, "end": 60, "text": "c"}]
    assert media._group_segments(segs, 600) == ["[10:00] a b", "[10:50] c"]


def test_ffmpeg_helpers(tmp_path: Path, monkeypatch):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", str(video)], check=True)
    assert 3.5 < media.probe_duration(video) < 4.5
    assert media.has_video_stream(video)
    frames = media.extract_frames(video, tmp_path, count=3)
    assert len(frames) == 3

    monkeypatch.setenv("TRANSCRIPTION_API_KEY", "k")
    from app.config import get_settings

    get_settings.cache_clear()
    seen = []
    monkeypatch.setattr(media, "_transcribe_segment", lambda part, offset: seen.append(part) or [f"[{media.fmt_ts(offset)}] bip"])
    assert media.transcribe_file(video, tmp_path) == "[0:00] bip"
    assert seen and seen[0].suffix == ".mp3"
    get_settings.cache_clear()
