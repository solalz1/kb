"""The wiki as an Obsidian vault: a zip to open right away, and an optional two-way sync through a private GitHub
repository (the vault on the Mac is a clone of it, kept in step by the Obsidian Git plugin).

The vault:
    index.md, log.md            written by the KB (an edit there is replaced at the next sync)
    SCHEMA.md                   the user's conventions for Claude (editable: read back into the app)
    Wiki/<Section>/<Title>.md   the pages (editable: the user's version replaces the KB's, his edit wins)
    Sources/<Title> (<ref>).md  the items the pages cite, read-only (the raw layer)
    .obsidian/graph.json        graph colours by section, written once (then the user's)

Sync, every WIKI_GIT_INTERVAL seconds and after each change of the wiki, in the wiki's worker thread: first read what
changed in the repository since the last pass (edits made in Obsidian are imported: content, title, section, folder,
frontmatter, deletion; each note on its own, so one that can't be taken, a title clash say, is set aside and reported
without stopping the others), then write the KB's version of every file that differs, in one commit.

The KB only overwrites or removes a note it owns: one it wrote (or read back into the wiki) and that hasn't changed
in the repository since. Notes of the user's that aren't pages (another folder, a note in Sources/, a second note
with a page's name) are never touched. Nothing runs without WIKI_GIT_REPO and WIKI_GIT_TOKEN.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath

import httpx

from . import db, wiki
from .config import get_settings
from .export import _slug

log = logging.getLogger(__name__)

API = "https://api.github.com"
STATE_KEY = "wiki_git"
MARKER = "%% kb:sources"
SCHEMA_NOTE = ("%% Tes conventions pour Claude quand il écrit ce wiki, en plus des siennes. Modifie-les ici ou dans "
               "l'app (Réglages > Wiki) : elles sont reprises à la synchro suivante. %%")
GRAPH_COLORS = {          # section -> RGB, after the app's palette
    "personal": 0xB5835A, "research": 0x4F7CAC, "reading": 0x6E8B4E, "business": 0xC9893B, "competitive": 0x8B5E9A,
}
SOURCES_COLOR = 0xA8A29A
ONCE = (".obsidian/graph.json", ".gitignore")       # written if missing, then left to the user
_lock = threading.Lock()


class GitError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status} : {message}")
        self.status = status


def enabled() -> bool:
    s = get_settings()
    return bool(s.wiki_git_repo and s.wiki_git_token and "/" in s.wiki_git_repo)


def _lang() -> str:
    return "en" if get_settings().kb_language == "en" else "fr"


# ---------------------------------------------------------------------------
# The vault
# ---------------------------------------------------------------------------

def page_path(page: dict, lang: str | None = None) -> str:
    """Wiki/<Section>/<Title>.md, or under a folder of the user's within the section (metadata.folder)."""
    folder = (db.loads(page.get("metadata")) or {}).get("folder")
    return (f"Wiki/{wiki.section_label(page['section'], lang or _lang())}/" + (f"{folder}/" if folder else "")
            + f"{page['title']}.md")


def source_name(item: dict) -> str:
    return f"{_slug(item.get('title') or 'sans titre', 70)} ({wiki.ref(item['id'])})"


OUR_KEYS = ("kb_id", "section", "type", "summary", "sources", "updated", "updated_by")


def _fm(values: dict, extra: str | None = None) -> list[str]:
    """Frontmatter: the KB's keys, then the user's own (tags, aliases…) exactly as he wrote them."""
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in values.items() if v not in (None, "", [])]
    return ["---"] + lines + ([extra] if extra else []) + ["---", ""]


def render_page(page: dict, sources: list[dict], names: dict[str, str], lang: str | None = None) -> str:
    """A page as a Markdown note: frontmatter, the content ([src:ref] become links to the source notes), then the list
    of sources after a marker, rebuilt at every sync and dropped when the note is read back."""
    lang = lang or _lang()

    def cite(m: re.Match) -> str:
        name = names.get(m.group(1).lower())
        return f"[[Sources/{name}|↗]]" if name else m.group(0)

    body = wiki.CITE_RE.sub(cite, page["content"]).strip()
    extra = (db.loads(page.get("metadata")) or {}).get("frontmatter")
    lines = _fm({
        "kb_id": page["id"],
        "section": wiki.section_label(page["section"], lang),
        "type": wiki.KIND_LABELS.get(page["kind"], page["kind"]),
        "summary": page.get("summary"),
        "sources": len(sources),
        "updated": str(page.get("updated_at") or "")[:10],
        "updated_by": "toi" if page.get("updated_by") == "user" else "claude",
    }, extra)
    lines.append(body or "*(page vide)*")
    if sources:
        lines += ["", f"{MARKER} : liste écrite par la KB à chaque synchro %%", "## Sources"]
        for s in sources:
            when = str(s.get("published_at") or s.get("created_at") or "")[:10]
            lines.append(f"- [[Sources/{names[wiki.ref(s['id'])]}|{(s.get('title') or 'sans titre').replace(']', ' ')}]]"
                         + (f" · {when}" if when else ""))
    return "\n".join(lines).rstrip() + "\n"


def render_source(item: dict, cited_by: list[str]) -> str:
    lines = _fm({"kb_id": item["id"], "type": item.get("kind"), "source": item.get("source_url"),
                 "author": item.get("author"), "published": str(item.get("published_at") or "")[:10] or None,
                 "saved": str(item.get("created_at") or "")[:10], "kb": get_settings().item_url(item["id"])})
    lines.append(f"*{item.get('title') or 'sans titre'}*" + (f" — [source]({item['source_url']})" if item.get("source_url") else ""))
    if item.get("summary"):
        lines += ["", item["summary"]]
    if item.get("key_points"):
        lines += [""] + [f"- {p}" for p in item["key_points"]]
    if cited_by:
        lines += ["", "Cité dans : " + ", ".join(f"[[{t}]]" for t in cited_by)]
    lines += ["", "%% Source en lecture seule : la KB réécrit cette note à chaque synchro. %%"]
    return "\n".join(lines).rstrip() + "\n"


def render_index(pages: list[dict], lang: str | None = None) -> str:
    lang = lang or _lang()
    lines = ["# Index du wiki", "",
             f"*Écrit par la KB à chaque changement · {len(pages)} page{'s' if len(pages) > 1 else ''}.*", ""]
    for key in wiki.SECTIONS:
        rows = [p for p in pages if p["section"] == key]
        if not rows:
            continue
        lines.append(f"## {wiki.section_label(key, lang)}")
        for p in rows:
            n = p.get("n_sources") or 0
            lines.append(f"- [[{p['title']}]]" + (f" — {p['summary']}" if p.get("summary") else "")
                         + (f" · {n} source{'s' if n > 1 else ''}" if n else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_schema() -> str:
    return f"# Schéma du wiki\n\n{SCHEMA_NOTE}\n\n{wiki.user_schema()}".rstrip() + "\n"


def _graph() -> str:
    groups = [{"query": f'path:"Wiki/{wiki.section_label(k, _lang())}"', "color": {"a": 1, "rgb": rgb}}
              for k, rgb in GRAPH_COLORS.items()]
    groups.append({"query": 'path:"Sources"', "color": {"a": 1, "rgb": SOURCES_COLOR}})
    return json.dumps({"colorGroups": groups, "showTags": False, "showAttachments": False, "hideUnresolved": False,
                       "showOrphans": True}, indent=2) + "\n"


def _sources_of(page_ids: list[str], c=None) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """For each page, its source items, newest first; and every such item by id."""
    if not page_ids:
        return {}, {}
    sql = """select s.page_id::text as page_id, i.id::text as id, i.title, i.kind, i.source_url, i.author,
                    i.published_at, i.created_at, i.summary, i.key_points
             from wiki_page_sources s join items i on i.id = s.item_id where s.page_id = any(%s::uuid[])
             order by coalesce(i.published_at, i.created_at) desc, i.id"""
    rows = c.execute(sql, (page_ids,)).fetchall() if c else db.fetchall(sql, (page_ids,))
    by_page: dict[str, list[dict]] = {}
    items: dict[str, dict] = {}
    for r in rows:
        it = {k: v for k, v in r.items() if k != "page_id"}
        items[it["id"]] = it
        by_page.setdefault(r["page_id"], []).append(it)
    return by_page, items


def render_vault(page_of: dict[str, str] | None = None) -> dict[str, str]:
    """Every file of the vault the KB writes: path -> content. `page_of`, if given, gets each page note's page id."""
    pages = wiki.all_pages(with_content=True)
    by_page, items = _sources_of([p["id"] for p in pages])
    names = {wiki.ref(i): source_name(it) for i, it in items.items()}
    title_of = {p["id"]: p["title"] for p in pages}
    files: dict[str, str] = {
        "index.md": render_index(pages),
        "log.md": wiki.log_markdown(),
        "SCHEMA.md": render_schema(),
        ".obsidian/graph.json": _graph(),
        ".gitignore": ".obsidian/workspace*.json\n.obsidian/cache\n.trash/\n.DS_Store\n",
    }
    cited_by: dict[str, list[str]] = {}
    for p in pages:
        srcs = by_page.get(p["id"], [])
        files[page_path(p)] = render_page(p, srcs, names)
        if page_of is not None:
            page_of[page_path(p)] = p["id"]
        for s in srcs:
            cited_by.setdefault(s["id"], []).append(title_of[p["id"]])
    for i, it in items.items():
        files[f"Sources/{names[wiki.ref(i)]}.md"] = render_source(it, sorted(cited_by.get(i, [])))
    return files


def export_zip_file() -> str:
    """The whole vault in a zip (temporary file, its path returned): unzip it and open the folder in Obsidian."""
    files = render_vault()
    fd, path = tempfile.mkstemp(prefix="kb-wiki-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, content in sorted(files.items()):
                z.writestr(f"KB Wiki/{name}", content)
    except BaseException:
        os.unlink(path)
        raise
    return path


# ---------------------------------------------------------------------------
# Reading a note edited in Obsidian back into the wiki
# ---------------------------------------------------------------------------

_SOURCE_LINK = re.compile(r"\[\[Sources/[^\[\]|\n]*?\(([0-9a-f]{8})\)(?:\.md)?(?:\|[^\[\]\n]*)?\]\]", re.I)
_SOURCE_ITEM = re.compile(r"^- \[\[Sources/")


def _frontmatter(text: str) -> tuple[dict, str, str]:
    """(the KB's keys, the user's other keys as written, the rest of the note)."""
    if not text.startswith("---\n"):
        return {}, "", text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, "", text
    block, rest = text[4:end], text[end + 4:].lstrip("\n")
    groups: list[tuple[str, list[str]]] = []
    for line in block.splitlines():
        if line and not line[0].isspace() and not line.startswith("- ") and ":" in line:
            groups.append((line.split(":", 1)[0].strip(), [line]))
        elif groups:
            groups[-1][1].append(line)          # an indented line or a list item: part of the key above
    ours, extra = {}, []
    for key, lines in groups:
        if key not in OUR_KEYS:
            extra += lines
            continue
        value = lines[0].split(":", 1)[1].strip()
        if len(lines) > 1:              # written over several lines (summary: > …): its lines joined
            more = " ".join(line.strip() for line in lines[1:] if line.strip())
            value = more if value in ("", ">", "|", ">-", "|-", ">+", "|+") else f"{value} {more}"
            ours[key] = value.strip("'\"")
            continue
        try:
            ours[key] = json.loads(value)
        except ValueError:
            ours[key] = value.strip("'\"")
    return ours, "\n".join(extra).strip(), rest


def parse_note(text: str) -> tuple[dict, str, str]:
    """(the KB's frontmatter keys, the user's own frontmatter, the body). The body's source links go back to [src:ref];
    the KB's list of sources is dropped, but anything the user wrote below it is kept."""
    text = (text or "").replace("\r\n", "\n")
    meta, extra, body = _frontmatter(text)
    cut = body.find(MARKER)
    if cut >= 0:
        head, tail = body[:cut], body[cut:].split("\n")[1:]
        if tail and tail[0].strip() == "## Sources":
            tail = tail[1:]
        kept = "\n".join(line for line in tail if not _SOURCE_ITEM.match(line)).strip()
        body = head.rstrip() + (f"\n\n{kept}" if kept else "")
    body = _SOURCE_LINK.sub(lambda m: f"[src:{m.group(1).lower()}]", body)
    return meta, extra, body.strip()


def _importable(path: str) -> bool:
    return path == "SCHEMA.md" or (path.startswith("Wiki/") and path.endswith(".md"))


def _place(path: str) -> tuple[str | None, str | None]:
    """(section, folder of the user's within it) from a note's path: Wiki/Lectures/Camus/Sisyphe.md -> reading, Camus.
    A first folder that isn't a section stays as a folder (its section then comes from elsewhere)."""
    parts = PurePosixPath(path).parts[1:-1]
    if not parts:
        return None, None
    section = wiki.section_from_label(parts[0])
    folder = "/".join(parts[1:] if section else parts) or None
    return section, folder


def import_note(c, path: str, text: str, known_sha: str | None, seen: set[str], remote: dict[str, str],
                first: bool = False, owned: dict[str, str] | None = None) -> tuple[str | None, bool]:
    """A note added or changed in the repository: the user's version becomes the page (his edit wins).

    Returns (the page's title if it changed, whether this note is now the page's own note). Raises wiki.Conflict for a
    note that can't be taken (its title belongs to another page whose note is still there). `first`: the first pass
    with this repository, when nobody knows which side is newer: a note of a page the wiki has gets the KB's version,
    only notes of pages it doesn't have are taken. `owned`: the paths of the notes the KB wrote (a page's own notes)."""
    owned = owned or {}
    if path == "SCHEMA.md":
        if first and wiki.user_schema():
            return None, True
        _, _, body = parse_note(text)
        body = body.removeprefix("# Schéma du wiki").strip().replace(SCHEMA_NOTE, "").strip()
        if body != wiki.user_schema():
            wiki.set_user_schema(body, source="obsidian")
            return "SCHEMA.md", True
        return None, True
    meta, extra, body = parse_note(text)
    title = wiki.clean_title(PurePosixPath(path).stem)
    folder_section, folder = _place(path)
    page = None
    kb_id = str(meta.get("kb_id") or "")
    if kb_id:
        cand = c.execute(f"select {wiki.PAGE_FIELDS} from wiki_pages where id::text = %s and not archived",
                         (kb_id,)).fetchone()
        own = page_path(cand) if cand else None
        if cand and (own == path or own not in remote or own not in owned):
            page = cand                 # its own note, or the note renamed or moved (its old note is gone)
        # otherwise a copy of a page's note (Obsidian's "Make a copy"): a new page
    if page is None:
        cand = wiki.by_title(title, c)
        if cand:
            if page_path(cand) == path and first:
                return None, True       # first pass: the KB's version of its page
            if page_path(cand) == path and path in owned:
                page = cand             # the page's own note, its frontmatter gone
            else:
                # a note of his that only shares the name of a page: never taken for that page
                raise wiki.Conflict(f"« {path} » : une autre page s'appelle déjà « {cand['title']} »")
    if first and page is not None:
        return None, page_path(page) == path
    old_meta = db.loads((page or {}).get("metadata")) or {}
    section = folder_section or wiki.section_from_label(meta.get("section")) or (page or {}).get("section") or "research"
    content = wiki._normalize_content(body, title)
    summary = meta.get("summary") if isinstance(meta.get("summary"), str) else (page or {}).get("summary")
    if page and (content, title, section, summary, extra or None, folder) == (
            page["content"], page["title"], page["section"], page.get("summary"), old_meta.get("frontmatter"),
            old_meta.get("folder")):
        seen.add(page["id"])
        return None, True
    lost = None
    if page and known_sha is not None and _blob_sha(render_page_for_sha(page, c)) != known_sha:
        # Claude changed the page since the last pass, and the user edited the copy he had: his version wins, the
        # night's review puts back the facts of Claude's update that he never saw
        lost = {"revision": page["revision"], "at": datetime.now(timezone.utc).isoformat()}
    row = wiki.save_page(c, page=page, title=title, section=section, kind=(page or {}).get("kind") or "theme",
                         summary=summary, content=content, author="user", reason="obsidian",
                         metadata={"frontmatter": extra or None, "folder": folder,
                                   **({"lost_update": lost} if lost else {})})
    seen.add(row["id"])
    wiki.add_log("edit", row["title"], {"pages": [row["title"]], "source": "obsidian",
                                        **({"replaced_claude_update": True} if lost else {})}, c)
    return row["title"], True


def render_page_for_sha(page: dict, c=None) -> str:
    """The note of one page exactly as render_vault writes it (to tell whether it changed since the last pass)."""
    by_page, items = _sources_of([page["id"]], c)
    names = {wiki.ref(i): source_name(it) for i, it in items.items()}
    return render_page(page, by_page.get(page["id"], []), names)


def archive_note(c, path: str, seen: set[str], moved: set[str]) -> str | None:
    """A page's note deleted in Obsidian: the page leaves the wiki (kept, with its history). Not when the note was
    only renamed or moved (its page is in `moved`), nor for a note that wasn't a page's."""
    if not (path.startswith("Wiki/") and path.endswith(".md")):
        return None
    page = wiki.by_title(PurePosixPath(path).stem, c)
    if not page or page["id"] in seen or page["id"] in moved or page_path(page) != path:
        return None
    wiki.archive_page(c, page, reason="obsidian: supprimée")
    seen.add(page["id"])
    wiki.add_log("edit", page["title"], {"deleted": True, "source": "obsidian"}, c)
    return page["title"]


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=API, timeout=60)
    return _client


def _gh(method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = ()) -> dict | list | None:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.wiki_git_token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    r = _http().request(method, f"/repos/{s.wiki_git_repo}{path}", json=body, headers=headers)
    if r.status_code in ok:
        return None
    if r.status_code >= 400:
        try:
            message = r.json().get("message") or r.text[:300]
        except ValueError:
            message = r.text[:300]
        raise GitError(r.status_code, message)
    return r.json() if r.content else {}


def _blob_sha(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


README = ("# KB Wiki\n\nCoffre Obsidian synchronisé avec la KB : ouvre ce dossier dans Obsidian, avec le plugin "
          "Obsidian Git pour garder les deux en phase.\n")


def _head() -> str:
    """The branch's last commit. An empty repository (GitHub answers 409, or 404 for the branch) first gets a README,
    so that there is a branch to commit on."""
    s = get_settings()
    try:
        return _gh("GET", f"/git/ref/heads/{s.wiki_git_branch}")["object"]["sha"]
    except GitError as exc:
        if exc.status not in (404, 409):
            raise
        try:
            _gh("PUT", "/contents/README.md", {"message": "KB wiki: first commit", "branch": s.wiki_git_branch,
                                               "content": base64.b64encode(README.encode()).decode()})
        except GitError as again:
            raise GitError(exc.status, f"dépôt {s.wiki_git_repo} inaccessible avec ce jeton, ou branche "
                                       f"« {s.wiki_git_branch} » introuvable ({again})") from again
        return _gh("GET", f"/git/ref/heads/{s.wiki_git_branch}")["object"]["sha"]


def _tree(commit_sha: str) -> tuple[str, dict[str, str]]:
    commit = _gh("GET", f"/git/commits/{commit_sha}")
    tree_sha = commit["tree"]["sha"]
    tree = _gh("GET", f"/git/trees/{tree_sha}?recursive=1")
    if tree.get("truncated"):
        raise GitError(500, "dépôt trop volumineux pour l'API (arbre tronqué)")
    return tree_sha, {e["path"]: e["sha"] for e in tree.get("tree") or [] if e.get("type") == "blob"}


def _blob(sha: str) -> str:
    data = _gh("GET", f"/git/blobs/{sha}")
    raw = base64.b64decode(data.get("content") or "") if data.get("encoding") == "base64" else (data.get("content") or "").encode()
    return raw.decode("utf-8", errors="replace")


def get_state() -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (STATE_KEY,))
    return dict(row["value"]) if row else {}


def _save_state(**changes) -> dict:
    row = db.fetchone(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = kb_settings.value || excluded.value, updated_at = now()
           returning value""", (STATE_KEY, db.jsonb(changes)))
    return dict(row["value"])


def _kb_file(path: str) -> bool:
    """Files that are the KB's alone, whatever is in them: rewritten even if edited in the repository."""
    return path in ("index.md", "log.md", "SCHEMA.md") or (path.startswith("Sources/") and path.endswith(".md"))


def sync_once() -> dict:
    """One pass: import what changed in the repository since the last one, then commit the KB's version of the files
    it owns that differ. Returns what it did.

    State (kb_settings.wiki_git): `head` and `tree` of the last pass, `remote` (path -> blob sha of every file then),
    `files` (the notes the KB owns: path -> the blob it wrote or read back), `skipped` (notes set aside, for Réglages)."""
    if not enabled():
        return {"enabled": False}
    s = get_settings()
    with _lock:
        state = get_state()
        if state.get("repo") != s.wiki_git_repo or state.get("branch") != s.wiki_git_branch:
            state = _save_state(repo=s.wiki_git_repo, branch=s.wiki_git_branch, head=None, tree=None, remote={},
                                files={}, skipped=[], pages={})
        head = _head()
        first = not state.get("head")
        before: dict[str, str] = state.get("remote") or {}
        owned: dict[str, str] = dict(state.get("files") or {})
        if head == state.get("head") and state.get("tree"):
            tree_sha, remote = state["tree"], dict(before)      # nothing new in the repository: no need to read it
            fresh = False
        else:
            tree_sha, remote = _tree(head)
            fresh = True
        if not fresh and state.get("skipped"):
            tree_sha, remote = _tree(head)      # notes set aside are tried again at every pass
            fresh = True
        notes_of: dict[str, str] = dict(state.get("pages") or {})      # path -> page id, for the notes the KB wrote
        imported, deleted = [], []
        skipped = [x for x in state.get("skipped") or [] if x["path"] in remote]
        if fresh:
            retry = {x["path"] for x in skipped}
            changed = sorted(p for p, sha in remote.items()
                             if _importable(p) and (before.get(p) != sha or p in retry))
            removed = sorted(p for p in before if _importable(p) and p not in remote)
            notes = {p: _blob(remote[p]) for p in changed}
            ids = {p: str(parse_note(t)[0].get("kb_id") or "") for p, t in notes.items() if p != "SCHEMA.md"}
            moved = set(ids.values()) - {""}
            # a page's note replaced by another page's note renamed over it (that page's own note gone): the first
            # page's note is gone too. Pasted over it while the other note stays, it is an edit of the first page.
            displaced = []
            for p, kb_id in ids.items():
                page = wiki.by_title(PurePosixPath(p).stem) if kb_id else None
                other = db.fetchone(f"select {wiki.PAGE_FIELDS} from wiki_pages where id::text = %s and not archived",
                                    (kb_id,)) if page and page["id"] != kb_id else None
                if page and other and page_path(page) == p and page_path(other) not in remote:
                    displaced.append(p)
            seen: set[str] = set()
            skipped = [x for x in skipped if x["path"] not in notes]
            with db.conn() as c, c.transaction():
                for p in removed + displaced:   # first: a deleted page frees its title for a note renamed to it
                    try:
                        with c.transaction():
                            title = archive_note(c, p, seen, moved)
                    except wiki.Conflict as exc:
                        log.warning("Obsidian : %s non supprimée : %s", p, exc)
                        continue
                    if p in removed:
                        owned.pop(p, None)
                    if title:
                        deleted.append(title)
                for p in changed:
                    try:
                        with c.transaction():
                            title, mine = import_note(c, p, notes[p], owned.get(p), seen, remote, first, owned)
                    except wiki.Conflict as exc:
                        skipped.append({"path": p, "sha": remote[p], "reason": str(exc)})
                        owned.pop(p, None)
                        continue
                    if mine:
                        owned[p] = remote[p]
                    else:
                        owned.pop(p, None)
                    if title:
                        imported.append(title)
                for p in list(owned):
                    if p not in remote:
                        owned.pop(p)
                        notes_of.pop(p, None)
                if imported or deleted:     # before the vault is written, so that log.md has it already
                    wiki.add_log("sync", "Modifications d'Obsidian reprises",
                                 {"pages": [t for t in imported if t != "SCHEMA.md"], "deleted": deleted}, c)
            # what was read is now known, whatever happens next: it is never imported twice
            _save_state(head=head, tree=tree_sha, remote=remote, files=owned, skipped=skipped, pages=notes_of)

        def mine(p: str) -> bool:
            return p not in remote or owned.get(p) == remote[p]

        page_of: dict[str, str] = {}
        desired = render_vault(page_of)
        # a page whose note should move where a note of his already sits (an ingest changed its section): it stays in
        # its old note, and Réglages says so
        for new in [p for p in page_of if not mine(p)]:
            pid = page_of.pop(new)
            text = desired.pop(new)
            old = next((op for op, i in notes_of.items() if i == pid and op != new and op in remote
                        and owned.get(op) == remote[op] and op not in desired), None)
            if old:
                desired[old], page_of[old] = text, pid
            reason = ("une note à toi porte le nom d'une page : la page "
                      + (f"reste dans « {old} »" if old else "n'a pas de note pour l'instant"))
            skipped = [x for x in skipped if x["path"] != new] + [{"path": new, "sha": remote[new], "reason": reason}]
        writes = [{"path": p, "mode": "100644", "type": "blob", "content": text} for p, text in sorted(desired.items())
                  if remote.get(p) != _blob_sha(text)
                  and (p not in remote or (p not in ONCE and (_kb_file(p) or mine(p))))]
        removals = [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in sorted(remote)
                    if p not in desired and p.endswith(".md") and p.startswith(("Wiki/", "Sources/"))
                    and owned.get(p) == remote[p]]
        committed = None
        if writes or removals:
            n = len(writes) + len(removals)
            new_tree = _gh("POST", "/git/trees", {"base_tree": tree_sha, "tree": writes + removals})
            commit = _gh("POST", "/git/commits", {
                "message": f"KB wiki: {n} fichier{'s' if n > 1 else ''} mis à jour", "tree": new_tree["sha"],
                "parents": [head]})
            try:
                _gh("PATCH", f"/git/refs/heads/{s.wiki_git_branch}", {"sha": commit["sha"], "force": False})
            except GitError as exc:
                if exc.status == 422:   # someone pushed meanwhile: the next pass imports it first, then writes
                    raise GitError(422, "le dépôt a changé pendant la synchro : nouvel essai au prochain passage") from exc
                raise
            committed = head = commit["sha"]
            tree_sha = new_tree["sha"]
            for w in writes:
                remote[w["path"]] = owned[w["path"]] = _blob_sha(w["content"])
            for r in removals:
                remote.pop(r["path"], None)
                owned.pop(r["path"], None)
                notes_of.pop(r["path"], None)
        for p, pid in page_of.items():
            if owned.get(p) == remote.get(p):
                notes_of[p] = pid
        _save_state(head=head, tree=tree_sha, remote=remote, files=owned, skipped=skipped, pages=notes_of,
                    last_sync_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), last_error=None)
        return {"enabled": True, "imported": imported, "deleted": deleted, "skipped": [x["path"] for x in skipped],
                "written": len(writes), "removed": len(removals), "commit": committed}


def sync_safely() -> dict | None:
    try:
        return sync_once()
    except Exception as exc:
        log.warning("Synchro Obsidian échouée : %s", exc)
        try:
            _save_state(last_error=str(exc)[:500], last_error_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except Exception:  # noqa: BLE001
            log.warning("État de la synchro Obsidian non enregistré", exc_info=True)
        return None


def status() -> dict:
    s = get_settings()
    out: dict = {"configured": enabled()}
    if not out["configured"]:
        out["missing"] = [name for name, ok in (("WIKI_GIT_REPO", s.wiki_git_repo), ("WIKI_GIT_TOKEN", s.wiki_git_token))
                          if not ok]
        return out
    state = get_state()
    out.update(repo=s.wiki_git_repo, branch=s.wiki_git_branch, url=f"https://github.com/{s.wiki_git_repo}",
               skipped=state.get("skipped") or [],
               **{k: state.get(k) for k in ("last_sync_at", "last_error", "last_error_at")})
    return out


def request_sync() -> None:
    """« Synchroniser maintenant »: wakes the wiki's worker, or runs a pass here when no worker runs in this process."""
    if wiki.worker_alive():
        wiki.sync_wake.set()
        wiki.wake.set()
        return
    threading.Thread(target=sync_safely, name="kb-obsidian-once", daemon=True).start()
