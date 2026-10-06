# KB: a personal knowledge base

**English** · [Français](README.fr.md)

Share anything from your iPhone or Mac (a tweet or thread, an article, a YouTube or TikTok video, a PDF, a screenshot, a voice memo, a Word file…). Claude reads it, summarizes it, tags it and links it to what you already saved. Then you can find it, ask questions about it, and use it when you start a new project.

- **Capture**: the iOS/macOS Share button (a Shortcut), the web app, or straight from Claude.
- **Every format**: X (official API, threads included), web articles, arXiv, GitHub, YouTube, TikTok/Instagram/Vimeo/podcasts, audio, video, PDFs (scanned ones too), images, Word/PowerPoint/Excel, plain notes.
- **Sources always kept**: every card stores the original URL (e.g. `https://x.com/handle/status/…`), author, date and original file. Every chat answer cites its sources `[1]` with a link.
- **Chat + project mode**: "What does my KB say about…?" and "I'm starting this project, what in my KB can help?", which returns a sourced brief: what's useful, who to follow, and the blind spots.
- **Perso space**: a separate department for personal development. Write your principles, values, lessons, goals, habits and journal by hand in the app (or share anything with `#perso`). Your notes are kept word for word; Claude only adds a summary, tags and links.
- **Advice mode**: "Should I take this job?" The answer is built on *your* principles and values (always read in full), then your lessons and notes. It cites each one, names the tensions between them and ends with a next step. Also in Claude through the connector (`get_principles`).
- **Tech digest agent**: every morning, a digest of the last 24 hours ordered from the most general to the most technical (Hacker News, Hugging Face papers, rising GitHub repos, lab and engineer blogs, the people you follow on X), tuned to what you save. Every Monday, the week in review plus 4–5 projects you could ship that week, each with a plan and a deliverable. It learns from your saves, your Perso goals and your votes, and suggests engineers worth following. Read it in the app, by e-mail, or in Claude (`get_digest`).
- **Never lose anything**: optional live copy to Notion (one page per item, kept up to date), and a Markdown export with the original files that opens in Obsidian and imports into Notion.
- **Claude connector (MCP)**: your KB is available inside Claude (web, desktop, mobile) and Claude Code. Claude searches it, browses it by type, tag or person, and reads full items before answering. An "Ask in Claude" button in the app opens the question there, so it runs on your Claude plan instead of API credits.
- **Also**: automatic links between items (with the reason), duplicate detection, "rediscover" picks, extracted to-dos (tools to try, papers to read), people/tool/concept pages, Obsidian-compatible Markdown export.

> The app's interface is in French. Summaries, tags and chat answers follow `KB_LANGUAGE` (`fr`, `en`, …).

## Architecture

```
iPhone / Mac ──Share──▶ Shortcut ──POST /api/ingest──────┐
Web app (PWA, kb.example.com) ───────────────────────────┤
Claude (MCP connector) ── /mcp/<secret> ─────────────────┤
                                                         ▼
                      ┌──────────── Railway: 1 container ─────────────┐
                      │ FastAPI  ─  worker (threads)  ─  MCP server    │
                      │   extraction ▸ Claude Haiku ▸ chunks ▸ Voyage  │
                      │   ▸ auto links  │  RAG chat: Claude Sonnet     │
                      └───────────────────────┬────────────────────────┘
                                              ▼
                 Supabase: Postgres + pgvector (hybrid search) + Storage
                                              │
                        Notion (optional): one page per item, kept in sync

Digest agent (in the worker, every morning): HN · HF papers · GitHub · blogs · X ──▶ Claude ──▶ app / e-mail / Claude
```

| Folder | What's inside |
|---|---|
| `supabase/migrations/` | Schema: items, chunks (pgvector + FR/EN full text), links, actions, hybrid RRF search, job queue |
| `backend/app/` | FastAPI API, worker, one extractor per source, enrichment, chat, MCP server, export |
| `web/` | React PWA (Veille and Perso feeds, item page, note editor, chat, add, to-dos, settings), installable on iPhone and Mac |
| `SETUP.md` | Step-by-step setup (accounts, Supabase, Railway, domain, Claude connector) |
| `SHORTCUT.md` | Build the Share-button Shortcut |

## Privacy

The repository is public, your data is not.

- **Code and data are separate.** The repository holds only code: no domain, no ID, no key, no item. Your items live in your own Supabase project (row-level security on, no public access), your keys in Railway's environment variables, and the app answers nothing without your token. It also tells search engines not to index it.
- **What leaves your server, and why.** To process an item, its content goes to the Anthropic API (summary, tags, chat answers; API data isn't used to train models by default) and to Voyage AI (embeddings for search; opt out of training in the Voyage dashboard, see [SETUP.md](SETUP.md)). Audio and video go to your transcription provider (Groq or OpenAI), tweets are read through the X API, and the Notion copy, if you turn it on, writes to your own Notion workspace. The digest agent only reads public sources; to rank them it sends Claude a summary of your interests (top tags, people, Perso goals). Perso notes follow the same path and nothing else.
- **Backups.** The Supabase free plan has no backups. See [SETUP.md](SETUP.md), step 9, for the Notion copy, the full export and Supabase Pro.

## Quick start

Follow **[SETUP.md](SETUP.md)** (about an hour), then **[SHORTCUT.md](SHORTCUT.md)**.

Locally with Docker: `cp .env.example .env`, fill in the keys, then `docker compose up --build` and open http://localhost:8000.

Without Docker:

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://localhost:8000
# frontend (another terminal), proxies /api to the backend
cd web && npm install && npm run dev     # http://localhost:5173
# tests: need Postgres + pgvector, e.g. the docker compose one (port 54322)
cd backend && KB_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/kb_test pytest
```

The repo ships a `CLAUDE.md` and two project skills (`/debug-item`, `/add-source`) for working on it with Claude Code.

## Rough costs (heavy personal use)

| Item | Order of magnitude |
|---|---|
| Enriching one item (Claude Haiku 4.5) | ~1 cent, more for a long PDF |
| One question in the app | ~2 cents (Haiku) to ~20 cents (Fable); ~4 cents with the default Sonnet 5.5. Pick the model in the chat |
| Questions through the Claude connector | included in your Claude plan |
| Reading a tweet (X API) | $0.005 per post read |
| Embeddings (Voyage) | negligible |
| Transcription (Groq Whisper) | a few cents per hour of audio |
| Railway | ~$5/month |
| Supabase | $0 (free) or $25/month (Pro, with daily backups) |
| Notion copy | free (works on the Notion free plan) |
| Tech digest | ~$0.10–0.20/day of Claude API, plus $0.005 per X post read (capped by `DIGEST_X_MAX_POSTS`) |

## Known limits

- **Old X threads**: the API only searches the last 7 days, so share the **last** tweet of an older thread; everything before it is fetched.
- **YouTube** often blocks cloud server IPs. If transcripts are missing, set a residential proxy (`YOUTUBE_PROXY_URL`).
- **LinkedIn, private Instagram posts**: content behind a login. Share a screenshot instead.
- **Files over 50 MB**: Supabase free-plan limit. For a long video, share the link instead.

## License

MIT, see [LICENSE](LICENSE). Dependencies use permissive licenses (MIT, BSD, Apache-2.0, LGPL, Unlicense). To report a vulnerability, see [SECURITY.md](SECURITY.md).
