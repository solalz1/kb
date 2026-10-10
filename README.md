# KB: a personal knowledge base

**English** · [Français](README.fr.md)

Share anything from your iPhone or Mac (a tweet or thread, an article, a YouTube video, a PDF, a screenshot, a voice memo…). Claude reads it, summarizes it, tags it and links it to what you already saved. Then you search it, ask it questions, and get advice grounded in your own notes. Everything runs on your own accounts: you pay a few dollars a month in API usage and hosting, and nobody else sees your data.

## What it does

- **Capture from anywhere**: the Share button on iPhone and Mac (ready-made Shortcuts), the web app, or Claude itself.
- **Every format**: X posts and threads, web articles, arXiv, GitHub, YouTube, TikTok/Instagram/Vimeo, podcasts, audio, video, PDFs (scanned ones too), images, Word/PowerPoint/Excel, plain notes.
- **Sources kept**: each card keeps the original link, author, date and file. Every answer cites its sources with links.
- **Folders**: your own shelves (ML, Interviews…, add and rename them in the app). Claude files each new item in the one that fits; pick one yourself when you share, and the Shortcut always offers your current folders.
- **Ask your KB**: "What did I save about…?", or "I'm starting this project, what can help?", which returns a sourced brief.
- **Personal space**: write your principles, values, lessons, goals and a daily journal (with a calendar). Your words are kept as written. **Advice mode** answers a decision from *your* principles and cites them.
- **Morning tech digest** (optional): Hacker News, Hugging Face papers, rising GitHub repos, blogs and the people you follow on X, ranked by what you save. On Mondays, the week in review and project ideas.
- **In Claude**: a connector (MCP) lets Claude search and read your KB on web, desktop, mobile and Claude Code.
- **Never locked in**: optional live copy in Notion, and a full Markdown export (Obsidian-ready) with your original files.
- **Made for the phone**: an installable app, in English or French, with swipe gestures to pin, archive or delete. It shows what each service costs you, month by month.

## What you need

| Service | What for | Needed? | Cost |
|---|---|---|---|
| [Claude API](https://platform.claude.com) | summaries, answers, digest | yes | ~0.3 ¢ per item, ~4 ¢ per question |
| [Voyage AI](https://dashboard.voyageai.com) | search (embeddings) | yes | free up to 200 M tokens |
| [Supabase](https://supabase.com) | database and files | yes | free plan works ($25/month with backups) |
| [Railway](https://railway.com) | runs the app | yes | about $5/month |
| X developer account | reading tweets, the people you follow | for X links | $0.005 per post read (prepaid) |
| [Groq](https://console.groq.com) (or OpenAI) | transcribing audio and video | for audio/video | a few cents per hour of audio |
| Notion | live copy of your KB | no | free |

A GitHub account (to fork this repository) and, for the ready-made Shortcuts, a Mac. A domain name is optional: Railway gives you an address.

## Install (about an hour)

1. **Fork** this repository (top right on GitHub). It holds only code, so your fork can be public or private.
2. **Supabase**: create a project, paste [`supabase/migrations/20261002000000_init.sql`](supabase/migrations/20261002000000_init.sql) into the SQL Editor, then run it.
3. **Keys**: create the keys from the table above, plus two secrets with `openssl rand -hex 32` (your app password `KB_API_TOKEN` and the connector secret `KB_MCP_SECRET`).
4. **Railway**: **New Project → Deploy from GitHub repo** → your fork. Paste [`.env.example`](.env.example) filled in with your values into **Variables → Raw Editor**, then **Settings → Networking → Generate Domain**.
5. **The app**: open that address in Safari on your iPhone → Share → **Add to Home Screen**, then paste your `KB_API_TOKEN`.
6. **The Share button**: on a Mac, in a clone of your fork, run `python3 shortcuts/build.py --url https://your-address --sign`, then double-click the files it writes ([SHORTCUT.md](SHORTCUT.md)).

Every click, the optional parts (custom domain, Claude connector, Notion, digest, e-mail) and a troubleshooting table are in **[SETUP.md](SETUP.md)**.

## Try it on your computer first (10 minutes)

With Docker installed, and only a Claude key and a Voyage key:

```bash
cp .env.example .env
# fill in KB_API_TOKEN, KB_MCP_SECRET (openssl rand -hex 32), ANTHROPIC_API_KEY and VOYAGE_API_KEY
docker compose up --build
```

Open http://localhost:8000 and paste your `KB_API_TOKEN`. The database runs in Docker and files stay on your disk. Tweets need an X key, and audio and video need a transcription key.

## Updating

On GitHub, open your fork and click **Sync fork**: Railway redeploys on its own. The database schema updates itself when the app starts (`/api/health` shows `"schema": "ok"`), so an update never needs an SQL step.

If Railway doesn't redeploy, give its GitHub app access to your fork (GitHub → Settings → Applications → Railway).

## What it costs

With daily use, expect $5–15 a month: about $5 of Railway, then Claude, which is about a third of a cent per saved item (Claude Haiku 5.5), 0.3 to 20 cents per question depending on the model, and $0.10–0.20 a day for the digest. Add X if you save tweets. **Settings → Costs** shows what each service cost, this month and in all, in US dollars: synced with the service where it has an API for it (the X balance, and the Claude Console's own figure with an optional Admin key), estimated from the KB's own calls elsewhere ([SETUP.md](SETUP.md), step 12). Questions asked through the Claude connector run on your Claude plan, not on API credits.

## Privacy

The repository is public, your data is not.

- **Code and data are separate.** Your items live in your Supabase project (row-level security on, no public access) and your keys in Railway's variables. The app answers nothing without your token, and asks search engines not to index it.
- **What leaves your server.** To process an item, its content goes to the Anthropic API (summaries, answers; API data isn't used for training by default) and to Voyage AI (search; opt out of training in its dashboard, see [SETUP.md](SETUP.md)). Audio and video go to your transcription provider, tweets are read through the X API, and the Notion copy writes to your own workspace. The digest reads public sources and sends Claude a summary of your interests.
- **Backups.** The Supabase free plan has none: see [SETUP.md](SETUP.md), step 9.

## Known limits

- **Older X threads**: the X API only searches the last 7 days. Share the **last** post of an older thread: everything before it is fetched.
- **YouTube** often blocks cloud servers. If transcripts are missing, set a residential proxy (`YOUTUBE_PROXY_URL`).
- **Sites that block servers** (Medium and some news sites answer 403 to cloud servers): the Shortcut has your phone fetch the page and sends it, so you share as usual. Otherwise the app reads the page again as a browser would, then tries Jina Reader and the Wayback Machine. Member-only stories: the full text comes from a share in **Safari**, signed in (the Shortcut attaches the page's text).
- **LinkedIn, private Instagram**: behind a login. Share a screenshot instead.
- **Files over 50 MB**: the Supabase free-plan limit. For a long video, share the link.

## How it's built

```
iPhone / Mac ──Share──▶ Shortcut ──POST /api/ingest──────┐
Web app (installable PWA) ───────────────────────────────┤
Claude (MCP connector) ── /mcp/<secret> ─────────────────┤
                                                         ▼
                      ┌──────────── Railway: 1 container ─────────────┐
                      │ FastAPI  ─  worker (threads)  ─  MCP server    │
                      │   extraction ▸ Claude Haiku ▸ chunks ▸ Voyage  │
                      │   ▸ auto links  │  chat: Claude Sonnet         │
                      └───────────────────────┬────────────────────────┘
                                              ▼
                 Supabase: Postgres + pgvector (hybrid search) + Storage
                                              │
                        Notion (optional): one page per item, kept in sync

Digest agent (in the worker, every morning): HN · HF papers · GitHub · blogs · X ──▶ Claude ──▶ app / e-mail / Claude
```

| Folder | What's inside |
|---|---|
| `backend/app/` | FastAPI API, worker, one extractor per source, enrichment, chat, digest agent, MCP server, export |
| `web/` | the React app (feeds, item page, notes, journal, chat, digest, settings) |
| `supabase/migrations/` | the database schema, applied at every start |
| `shortcuts/` | the generator of the iPhone and Mac Shortcuts |

Developing without Docker:

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://localhost:8000
cd web && npm install && npm run dev     # http://localhost:5173, proxies /api to the backend
# tests need Postgres + pgvector, e.g. the docker compose one (port 54322)
cd backend && KB_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/kb_test pytest
```

The repo ships a `CLAUDE.md` and two skills (`/debug-item`, `/add-source`) for working on it with Claude Code.

## License

MIT, see [LICENSE](LICENSE). To report a vulnerability, see [SECURITY.md](SECURITY.md).
