"""Stockage des fichiers : Supabase Storage (bucket privé) ou disque local en dev."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import get_settings


def _supabase_enabled() -> bool:
    s = get_settings()
    return bool(s.supabase_url and s.supabase_service_key)


def _headers(extra: dict | None = None) -> dict:
    key = get_settings().supabase_service_key
    headers = {"apikey": key}
    # Les anciennes clés service_role sont des JWT ; les nouvelles clés secrètes (sb_secret_…) non.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    if extra:
        headers.update(extra)
    return headers


def _object_url(path: str, prefix: str = "object") -> str:
    s = get_settings()
    return f"{s.supabase_url.rstrip('/')}/storage/v1/{prefix}/{s.storage_bucket}/{quote(path)}"


def upload(path: str, data: bytes, content_type: str | None = None) -> str:
    content_type = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if _supabase_enabled():
        r = httpx.post(
            _object_url(path),
            content=data,
            headers=_headers({"Content-Type": content_type, "x-upsert": "true"}),
            timeout=300,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Upload Storage échoué ({r.status_code}) : {r.text[:300]}")
    else:
        target = Path(get_settings().local_storage_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return path


def download(path: str) -> bytes:
    if _supabase_enabled():
        r = httpx.get(_object_url(path, "object/authenticated"), headers=_headers(), timeout=300)
        if r.status_code >= 400:
            r = httpx.get(_object_url(path), headers=_headers(), timeout=300)
        r.raise_for_status()
        return r.content
    return (Path(get_settings().local_storage_dir) / path).read_bytes()


def delete(path: str) -> None:
    if not path:
        return
    if _supabase_enabled():
        s = get_settings()
        httpx.request(
            "DELETE",
            f"{s.supabase_url.rstrip('/')}/storage/v1/object/{s.storage_bucket}",
            json={"prefixes": [path]},
            headers=_headers(),
            timeout=60,
        )
    else:
        p = Path(get_settings().local_storage_dir) / path
        if p.exists():
            p.unlink()


def local_signature(path: str) -> str:
    key = get_settings().kb_api_token.encode() or b"dev"
    return hmac.new(key, path.encode(), hashlib.sha256).hexdigest()[:32]


def signed_urls(paths: list[str], expires_in: int = 3600) -> dict[str, str]:
    """Signature groupée (un seul appel réseau pour toute une page de résultats)."""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not paths:
        return {}
    if not _supabase_enabled():
        return {p: signed_url(p) for p in paths}
    s = get_settings()
    try:
        r = httpx.post(
            f"{s.supabase_url.rstrip('/')}/storage/v1/object/sign/{s.storage_bucket}",
            json={"expiresIn": expires_in, "paths": paths},
            headers=_headers(),
            timeout=20,
        )
        r.raise_for_status()
        out = {}
        for entry in r.json():
            signed = entry.get("signedURL") or entry.get("signedUrl")
            if signed and entry.get("path"):
                out[entry["path"]] = f"{s.supabase_url.rstrip('/')}/storage/v1{signed}"
        return out
    except Exception:
        return {}


def signed_url(path: str | None, expires_in: int = 3600) -> str | None:
    """URL temporaire pour afficher/télécharger un fichier privé."""
    if not path:
        return None
    if _supabase_enabled():
        s = get_settings()
        try:
            r = httpx.post(
                _object_url(path, "object/sign"),
                json={"expiresIn": expires_in},
                headers=_headers(),
                timeout=20,
            )
            r.raise_for_status()
            signed = r.json().get("signedURL") or r.json().get("signedUrl")
            return f"{s.supabase_url.rstrip('/')}/storage/v1{signed}" if signed else None
        except Exception:
            return None
    return f"/api/local-files/{quote(path)}?sig={local_signature(path)}"
