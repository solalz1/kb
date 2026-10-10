# KB — context for Claude Code

A personal knowledge base: the user shares anything (tweet, article, video, PDF, image, note) from iPhone/Mac, the
backend extracts it, has Claude summarize it, indexes it (pgvector + full text), and it can be queried through the
PWA, the API or the MCP server (Claude connector). **The UI is bilingual**: French strings in the code are the keys of `t()` (`web/src/i18n.ts`), their English lives
in `web/src/i18n/en/*.ts`, and `npm run build` fails on a missing translation (a key may carry a context after `|`,
never shown in French: `t("Annuler|undo")`). LLM prompts are in French. Items
keep their card in a second language in `items.translations` (`localized()` in the front); tags are in English. Write new code comments and commit messages in English (older modules still have
French comments). Public docs are in English with `*.fr.md` French copies:
update both.

## Architecture

- `supabase/migrations/`: Postgres schema. **Idempotent**, safe to re-run: `app/migrate.py` applies it at every start
  (advisory lock, `AUTO_MIGRATE`, status in `/api/health`), so a migration must never destroy data. `vector(1024)` is
  fixed (= `EMBED_DIM`).
  Key SQL functions: `hybrid_search` (RRF of vectors + full text with the `kb` FR/EN config, `filter_spaces`),
  `similar_items`, `claim_next_item` (job queue, recovers stale items, stops after `max_attempts`). The `items_touch`
  trigger sets `notion_synced_at = null` whenever visible content changes (that's how the Notion copy knows what to
  rewrite); views (`view_count`) don't count.
- Two **spaces** on `items.space`: `main` (« Veille », what the user captures) and `perso` (personal development:
  principles, values, lessons, goals… in `items.category`). Vocabulary in `backend/app/taxonomy.py`, mirrored in
  `web/src/perso.ts`. Principles and values form the « charte », always sent in full in advice mode.
- `backend/app/` (FastAPI, Python 3.12, sync code + threads):
  - `main.py`: `/api/*`, the SPA, and `RootApp`, which routes `/mcp` (Bearer header) and `/mcp/<KB_MCP_SECRET>`
    to the MCP server.
  - `pipeline.py`: `ingest()` (queueing + dedup, `#perso`/`#leçon` hashtag routing), `create_note()` (hand-written
    notes, kept verbatim) and `process()` (extract → `llm.enrich` → chunks → embeddings → links). A title or tags set by
    the user (`metadata.manual_title`, `metadata.user_tags`) survive reprocessing.
  - `worker.py`: threads calling `claim_next_item()`. `ExtractionError` = permanent failure; any other exception = retry.
  - `extractors/`: one module per source. URL routing in `urls.classify()`, then `extractors/__init__.py`.
    PDFs use pypdfium2 (not thread-safe: always go through `_PDFIUM_LOCK`).
  - `llm.py`: every Claude call. Structured answers go through `call_tool`: a forced `tool_choice`, or structured
    outputs (`output_config.format`, `strict_schema`) on models that refuse forced tools (Sonnet 5.5, Opus 5.5). Perso
    items get extra rules and a `category`. These models also think before answering, and `max_tokens` counts it.
    Haiku 5.5 (the enrich model) accepts forced tools, which never think. Calls without a forced tool think by default
    on the 5.x models and get `THINKING_ROOM` on top of their `max_tokens`; Settings → Réflexion (`kb_settings` key
    `thinking`) turns it off where the API allows: Haiku 5.5 `disabled`, Sonnet 5.5 `between_tools` (Opus 5.5 and
    Fable always think).
  - `chat.py`: RAG, project mode and advice mode (`advise`: charter + Perso search + a little Veille), SSE.
  - `notion.py`: optional one-way copy to a Notion database (API version `2026-03-11`, data sources, `markdown` page
    content, `in_trash`). A `Syncer` thread in the worker, throttled to ~3 req/s; deleted items go through the
    `notion_trash` table; database IDs live in `kb_settings`.
  - `costs.py`: Settings → Costs. Every paid call is recorded in `usage_log` (`costs.record*`, priced from the public
    price lists). The Claude Console Cost API (`ANTHROPIC_ADMIN_KEY`, cents per UTC day) and the X credit balance are
    synced and cached 10 min; tests never reach them (the autouse `billing_apis` fixture fakes `_http_get`).
  - `mcp_server.py`: `mcp` SDK **v2** (`MCPServer`, not `FastMCP`), stateless, JSON responses.
  - `digest/`: the tech-digest agent. `sources.py` (HN Algolia, Hugging Face daily papers, GitHub search, RSS via
    feedparser, followed people on X via `/tweets/search/recent`), `profile.py` (interest profile learned from tags,
    entities, saved tweet authors, Perso goals and digest votes; auto-follows/suggests engineers in `watch`),
    `following.py` (the user's X follows join `watch` as `x_follow`: small newest-first pages read before each daily
    digest, stopping at a known id, since X bills every account returned; importing older follows is a separate button),
    `agent.py` (Haiku picks → Sonnet writes the daily digest; Opus writes Monday's weekly digest and projects; the
    model only cites candidate ids, URLs always come from the sources; `Scheduler` thread in the worker, `run_due`),
    `render.py` (Markdown, e-mail HTML, SMTP). Tables: `watch`, `digests`, `digest_feedback`.
- `shortcuts/build.py`: generates the three iOS/macOS Shortcuts (binary plists, stdlib only; the token is an import
  question, never written). Signing needs macOS (`--sign`). `tests/test_shortcuts.py` checks them against the API.
- `web/`: React 19 + Vite PWA, no CSS framework. "Warm paper" design (`styles.css`): ivory ground (`--desk`), cream
  cards (`--surface`), brown ink, one terracotta accent (`--waterman`) for every action, a kraft tint (`--tint`) for
  the Perso space, "à redécouvrir" and stat tiles. Every card is the same sheet; the content type is a small colored
  `.dot` (`--dot-*`) next to its label. Newsreader (serif) for titles, quotes and the user's notes, Instrument Sans
  for the interface, Geist Mono for code only (fonts self-hosted with `@fontsource-variable`); 12–16px radii, pill
  chips, 44px touch targets. Desktop: sidebar with "Ajouter" as the primary button and Réglages at the bottom.
  Phone: no top bar; a five-slot tab bar with the + in the middle, and the Feed page head carries the À faire badge
  and the settings cog. Keep this visual language and these colors.
  Feed cards sit in `SwipeRow` (`components/Swipe.tsx`, touch and pen only): right to pin, left to archive or delete,
  each with an undo toast; a deletion waits 5 s before it reaches the API.

## Commands

```bash
cd backend && pytest -q        # Postgres+pgvector required (KB_TEST_DATABASE_URL, see tests/conftest.py)
cd web && npm run build        # i18n check + typecheck + build
cd backend && pytest -q tests/test_site.py   # the built site in Chromium (Playwright), skipped without web/dist
docker compose up --build      # everything locally on :8000
```

Before opening a PR: backend tests, `npm run build` and the browser tests must all pass (CI runs the three jobs:
`backend`, `web`, `site`). Add a browser test in `tests/test_site.py` for every new page or flow.

Tests never call external services: Claude is faked by the `fake_llm` fixture, embeddings by
`EMBEDDINGS_PROVIDER=fake`, storage by the local disk, Notion by an `httpx.MockTransport` (`tests/test_notion.py`),
digest sources and Claude by fakes in `tests/test_digest.py`. Every backend change comes with a test in `tests/`; run
`pytest` and `npm run build` before saying a task is done. CI (`.github/workflows/ci.yml`) runs both on every push.

## Known pitfalls

- X API: field sets are tried in order (`PARAM_SETS`) because X returns 400 on an unknown field. Responses may use
  `referenced_tweets`/`note_tweet` or `referenced_posts`/`note_post`: handle both.
- YouTube often blocks server IPs. Fallback order: transcript API → yt-dlp subtitles → audio + Groq. yt-dlp needs
  Deno (installed in the Dockerfile).
- Supabase Storage rejects non-ASCII keys: always go through `pipeline._safe_filename`.
- Database connection: *Session pooler* (port 5432), `prepare_threshold=None`. No LISTEN/NOTIFY.
- FastAPI serves the frontend from `STATIC_DIR`; in dev, Vite proxies `/api` and `/mcp` to :8000.
- Never hardcode a real domain or personal data: examples use `kb.example.com`; the real values live in env vars.
  Demo data in tests must be fictional.
- The API doesn't enforce tool schemas: a card's lists once came back as one `<item>…</item>` text and the item page
  crashed on it. `llm.enrich` cleans lists (`_text_list`) and asks again when they're malformed, the schema file repairs
  stored rows at startup, the app cleans lists as they arrive (`cleanDetail` in `api.ts`), and `ErrorBoundary` keeps
  a page that fails to render from blanking the whole app.
- Some sites refuse servers (Medium: 403). `web.extract` falls back to the same request with a browser's TLS
  fingerprint (`curl_cffi`, `_browser_get`; faked in every test by the autouse `no_browser_fetch`), then Jina Reader,
  then the Wayback Machine (bot-check pages are rejected), then raises an `ExtractionError` that says what to do. A Safari share
  attaches the page's text (> `PAGE_TEXT_MIN` chars): `pipeline.ingest` keeps it in storage (`metadata.page_path`)
  and `extract_item` uses it when the server can't read the page or only got a sliver of it. For hosts that refuse
  servers (`web.KNOWN_BLOCKERS`, plus any that answered 403: `kb_settings.blocked_hosts`), the Shortcut sends
  `page_follows`, the API answers `page_wanted` and `page_url`, the item waits `PAGE_WAIT_SECONDS`, and the phone
  posts the page it fetched to `/api/items/<id>/page` (`metadata.page_html_path`, read first by `extract_item`).
- Perso notes are the user's own words: enrichment must never rewrite `content` for `kind = 'note'`, and the Notion
  copy and the export put the note text first, in full.

## Deployment

Railway (Dockerfile, one service: API + worker + MCP + frontend), Supabase (Postgres + Storage). Variables: see
`.env.example`. To diagnose a failed item: `debug-item` skill. To support a new source: `add-source` skill.
