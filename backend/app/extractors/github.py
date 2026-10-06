"""Dépôts GitHub : description, statistiques et README."""

from __future__ import annotations

from ..config import get_settings
from .base import ExtractionError, Extracted, http_client, parse_date


def extract(owner: str, repo: str, canonical: str) -> Extracted:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := get_settings().github_token:
        headers["Authorization"] = f"Bearer {token}"
    with http_client(headers=headers) as c:
        r = c.get(f"https://api.github.com/repos/{owner}/{repo}")
        if r.status_code == 404:
            raise ExtractionError("Dépôt GitHub introuvable ou privé")
        r.raise_for_status()
        info = r.json()
        rd = c.get(f"https://api.github.com/repos/{owner}/{repo}/readme", headers={"Accept": "application/vnd.github.raw"})
        readme = rd.text if rd.status_code == 200 else ""

    full = info.get("full_name", f"{owner}/{repo}")
    facts = [
        f"{info.get('stargazers_count', 0)} étoiles",
        f"langage : {info['language']}" if info.get("language") else None,
        f"licence : {(info.get('license') or {}).get('spdx_id')}" if info.get("license") else None,
        f"dernier push : {(info.get('pushed_at') or '')[:10]}",
        f"site : {info['homepage']}" if info.get("homepage") else None,
    ]
    header = f"{full} — {info.get('description') or ''}\n" + " · ".join(f for f in facts if f)
    if info.get("topics"):
        header += "\nTopics : " + ", ".join(info["topics"])
    return Extracted(
        kind="repo",
        title=full + (f" — {info['description']}" if info.get("description") else ""),
        content=header + "\n\nREADME :\n" + readme[:80_000],
        source_url=info.get("html_url") or canonical,
        author=(info.get("owner") or {}).get("login"),
        author_url=(info.get("owner") or {}).get("html_url"),
        site_name="GitHub",
        published_at=parse_date(info.get("created_at")),
        thumbnail_url=f"https://opengraph.githubassets.com/1/{full}",
        metadata={"stars": info.get("stargazers_count"), "language": info.get("language"), "topics": info.get("topics"),
                  "pushed_at": info.get("pushed_at"), "archived": info.get("archived")},
    )
