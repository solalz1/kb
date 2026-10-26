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

- `supabase/migrations/`: Postgres schema, one file per feature (`init`, `wiki`), applied in name order. **Idempotent**,
  safe to re-run: `app/migrate.py` applies it at every start
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
    notes, kept verbatim) and `process()` (extract → `llm.enrich` → chunks → embeddings → links). A title, tags or a
    folder set by the user (`metadata.manual_title`, `metadata.user_tags`, `metadata.manual_folder`) survive reprocessing.
    Titles, for any source (`choose_title`): a clean headline (`titles.looks_like_title`) stays word for word; what
    isn't one (a LinkedIn or X post's first words, a file name, « … | Site ») takes Claude's title, written by the rule
    in `llm._title_rule` (keep a real headline without the site's name, else write one). `fix_titles` retitled the
    cards saved before, once, at worker start (`kb_settings` `titles_fixed`, `llm.retitle`).
  - `folders.py`: the user's folders (`folders` table, one `items.folder_id` per item). Defaults are created once
    (`kb_settings` flag `folders_seeded`) at start; `llm.enrich` picks the folder of each new item, `sort_items`
    (`llm.classify_folders`, batches of 20) files the rest once at start (`folders_sorted`) and from the app. The
    Shortcut's "Où le ranger ?" list comes from `GET /api/folders/choices` (Automatique, the folders, Espace Perso) and
    goes back as `folder`; a folder chosen by hand is never changed by Claude.
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
  - `wiki.py`: the wiki (Karpathy's "LLM Wiki"): `wiki_pages` written by Claude, one per theme, concept, entity or book
    (never one per item), in five sections (`SECTIONS`: personal, research, reading, business, competitive). Content is
    Markdown with `[[Title]]` links (`links` keeps their lowercased targets, for backlinks) and `[src:xxxxxxxx]` item
    references (first 8 hex characters of the id; `wiki_page_sources` is derived from them). One queue, `wiki_jobs`
    (`ingest` after each `pipeline.process`, `answer`, `lint`), worked by one `WikiWorker` thread holding a lease in
    `kb_settings` (one worker across containers). Every write is `save_page`: a compare-and-swap on `revision` inside
    the caller's transaction (a job's pages are written together or not at all; `Conflict` retries the job), a row in
    `wiki_revisions`, and the log (`wiki_log`, rendered as log.md). Ingest = `llm.wiki_plan` (pages to update or
    create, from the index) then `llm.wiki_write` (the pages rewritten in full). The nightly review (`lint_due`, after
    `WIKI_LINT_HOUR`, only if the wiki changed) = `findings()` computed in SQL/Python (broken links, orphans, pages
    without sources, user edits that replaced an unseen Claude update) + `llm.wiki_lint` per batch of pages (fixes,
    merges, suggestions kept in `kb_settings.wiki_suggestions`, also fed to the weekly project ideas). The user's
    conventions (`kb_settings.wiki_schema`) go with every call. `chat.ask_wiki` answers from pages picked in the index
    (`llm.wiki_pick`), falling back to RAG when the wiki has nothing.
  - `obsidian.py`: the wiki as a vault (`render_vault`: index.md, log.md, SCHEMA.md, `Wiki/<Section>/<Title>.md`,
    `Sources/<Title> (<ref>).md`, `.obsidian/graph.json`), as a zip, and the optional two-way sync with a private GitHub
    repository through the Git Data API (`WIKI_GIT_*`). A pass imports the notes changed in the repository since the
    last one, each in its own savepoint (the user's version wins; a page Claude changed meanwhile gets
    `metadata.lost_update` for the review; a note that can't be taken is set aside in `skipped`), saves the state,
    then commits the KB's version of what differs, in one commit. **The KB only overwrites or removes a note it owns**
    (`files`: the blob it wrote or read back, unchanged since); index.md, log.md, SCHEMA.md and `Sources/` are its own.
    `kb_settings.wiki_git` keeps `head`, `tree`, `remote` (every file's blob sha), `files` and `skipped`. A page keeps
    the user's own frontmatter (`metadata.frontmatter`) and subfolder (`metadata.folder`).
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
- `web/`: React 19 + Vite PWA, no CSS framework. The pages follow the user's design canvas "KB — redesign chaud"
  (18 artboards, Mac and iPhone): match its sizes, spacing and wording when changing a page. "Warm paper" design
  (`styles.css`, phone first, the sidebar from 900px): ivory ground (`--desk`), cream cards (`--surface`), brown ink,
  one terracotta accent (`--accent`) for every action, kraft (`--tint`) for the Perso space, "à redécouvrir", related
  items and stat tiles, sand (`--sand`) for segmented controls. The content type is a small colored `.dot`
  (`--dot-*`). Newsreader (serif) for titles, quotes and the user's notes, Instrument Sans for the interface, Geist
  Mono for code only (self-hosted with `@fontsource-variable`). Icons are drawn in `src/icons.tsx` (no icon
  library). Desktop: sidebar (Ajouter, Veille, Perso, Wiki, Dossiers, Journal, Digest, Demander, À faire, Réglages at the
  bottom); the item page is two columns. Phone: no top bar, a five-slot tab bar with the + in the middle (the tab of
  the section a page belongs to is lit), Veille's head carries the wiki, folders, À faire and settings icons, Perso's the
  journal; Réglages is grouped rows that open `/settings/<section>`. `useDesktop()` (`layout.ts`) when a page differs
  by more than CSS.
  The wiki: `/wiki` (pages by section, the log, the review's ideas) and `/wiki/:slug` (a page laid out like an item
  page); `wiki.ts` renders a page's Markdown (`[[links]]` and `[src:…]` become links the router handles). « Demander »
  has a Wiki / Éléments switch (`source` of `/api/chat`, remembered per device) and « Garder dans le wiki » on answers.
  `obsidian.ts` builds the `obsidian://` links (open the vault at index, a page's note by its `vault_path`, Obsidian's
  search); the vault name is per device in localStorage, `KB-Wiki` by default (= `obsidian.VAULT`, the zip's folder).
  Feed cards sit in `SwipeRow` (`components/Swipe.tsx`, touch and pen only): right to pin, left to archive or delete,
  each with an undo toast; a deletion waits 5 s before it reaches the API. A long press (touch) or a right click (mouse)
  opens the same actions in `components/ContextMenu.tsx` (portalled to body, at the finger, Escape/arrow keys).
  - **Launch**: `sw.js` serves the cached shell at once and refreshes it behind (`kb:update` message when it changed:
    the next pull to refresh reloads). A device that opened the app with the shared sign-in before (`kb_sso_ok` in
    localStorage) renders it at once instead of waiting for `/api/health`; a 401 still ends on the sign-in page.
  - **Data comes from an in-memory cache** (`cache.ts`, keys and fetchers in `queries.ts`): `useQuery(key, fetcher)`
    shows what is cached at once and refreshes it in the background (stale-while-revalidate); nothing is persisted, so
    a reload starts afresh. Every non-GET call in `api.ts` marks the whole cache stale (`changeStarted`/`changeDone`);
    answers to requests that started before a change are dropped when something is already shown, so optimistic
    updates (`patchItem`, `dropItem`) never flicker back; a change still running after 8 s stops holding refreshes
    back. While deleting something a page shows, pass its `useQuery` a null key (ItemPage `removing`, Feed `leaving`)
    so the refresh doesn't ask for it again (a 404 fails the browser tests); a key forgotten while shown is loaded
    again. The cache keeps at most 160 values (30 whole items), never evicting what is on screen. The other tabs are
    prefetched once the app is idle; an item page opens with its card from the lists (`summaryOf`), and
    `GET /api/items/{id}?view=false` refreshes it without counting a visit.
  - **Moving between pages**: a data router (`createBrowserRouter` in `main.tsx`, every page a route under `App`'s
    `<Outlet/>`; descendant `<Routes>` would drop view transitions). Import `Link`, `NavLink` and `useNavigate` from
    `nav.tsx`, which turn on view transitions; `motion.ts` sets `html[data-nav]` (push, back, tab, fade, none) and
    `styles.css` animates `::view-transition-*` accordingly (the tab bar and sidebar keep their own
    `view-transition-name`). Tab bar links are `viewTransition={false} replace`: tabs switch at once and don't stack in
    the history (tapping the tab you're in from a page opened in it goes back to its root, `stepsToRoot`); rail links
    switch at once too, and on ≥900px pushes are a quick cross-fade. A pointerdown during a transition skips it, and a
    click that landed on `<html>` (the browser hit-tests the snapshots) is re-sent to what is under it (`main.tsx`).
    Before a push, `motion.ts` keeps a still copy of the page being left (`.main-copy`, by history index): the edge
    swipe lays it under the page it drags away. `<ScrollRestoration getKey={scrollKey}>` keeps each tab's scroll position. Lists animate
    reorders with `useFlip`. Loading states are skeletons (`components/Skeleton.tsx`), never "Chargement…".
  - **Liquid Glass**: the tab bar is a floating glass capsule whose lens slides to the current tab (`--i`), the desktop
    sidebar a floating glass pane; toasts, menus and the composer are glass too (`--glass*` tokens). Hover styles live
    in `@media (hover: hover)`; pressed controls scale down with a spring.
  - **Installed app gestures** (`components/Gestures.tsx`, standalone + touch only): pull down from the top to refresh in
    place (`cache.refreshShown()`: what the page shows is asked for again, the spinner turns until it is back, nothing
    reloads), swipe from the left edge (`EDGE`) to go back; `SwipeRow` ignores touches starting in that edge.
  - **Feel** (`motion.ts`): `pressFeedback()` (installed once in `App`) sets `.is-pressed` on what a finger presses only
    once the touch is clearly a tap, not the start of a scroll (a mouse uses `:active` under `@media (pointer: fine)`);
    `haptic()` ticks under the finger (iOS 18+: the system switch's haptic) when a gesture is about to act (a long card
    swipe, the pull to refresh). Phone pages get `components/TitleBar.tsx`: once the page's `h1` has scrolled under the
    top of the screen, a glass bar shows it small (tap: back to the top).
  - **Dev copy** (`motion.ts` `devCopy()`): on a host whose first label ends in `-dev` (or starts with `dev.`), the app
    shows a DEV tag, names itself "KB dev", and Settings give the real KB's address (`realOrigin()`) for the Shortcut
    and the connector. The backend side is `DEV_COPY=true` (or a Railway service named `…-dev`, `config.py`): no
    worker, no migration, `db_pool_max` ≤ 5; digest jobs and « Synchroniser maintenant » (Notion, Obsidian) are
    forwarded to the real KB (`main._on_the_real_kb`: PUBLIC_BASE_URL + KB_API_TOKEN, `x-kb-forwarded` against loops),
    and `/api/health` gives the copy the real KB's address (`real_url`). SETUP.md step 15, `tests/test_dev_copy.py`.
    Such a copy must never need a schema change.

## Commands

```bash
cd backend && pytest -q        # Postgres+pgvector required (KB_TEST_DATABASE_URL, see tests/conftest.py)
cd web && npm run build        # i18n check + typecheck + build
cd backend && pytest -q tests/test_site.py   # the built site in Chromium (Playwright), skipped without web/dist
docker compose up --build      # everything locally on :8000
```

Before opening a PR: backend tests, `npm run build` and the browser tests must all pass (CI runs the three jobs:
`backend`, `web`, `site`). Add a browser test in `tests/test_site.py` for every new page or flow.

Tests never call external services: Claude is faked by the `fake_llm` fixture (the wiki's calls too: `_fake_wiki`
files a source into topic pages by its words), GitHub by `FakeGitHub` in `tests/test_wiki.py`; the wiki's queue is
worked with `wiki.drain()` (no worker runs in tests), embeddings by
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
- Browser tests and motion: pages slide for ~0.4 s and lists glide after a refresh. Raw touch gestures (`_swipe`, CDP
  touch events) must wait for `_settled(page)` (no view transition or finite animation running), and a test that
  matches a heading by name may match the card it just tapped on the previous page: prefer `.item-title`.
- Reddit refuses servers ("You've been blocked by network security", even to Jina Reader; anonymous JSON closed, API
  tokens only on approval): it is one of `web.KNOWN_BLOCKERS`, so the Shortcut sends the page from the phone and
  `extractors/reddit.py` reads it (schema.org JSON-LD, `<shreddit-post>`/`<shreddit-comment>`, old reddit). A post has
  one address, `https://www.reddit.com/r/<sub>/comments/<id>` (`urls.reddit_post`), whatever link pointed to it; an
  app's share link (`/r/<sub>/s/<code>`) is resolved by the phone following it. `web.BLOCK_PAGE` must recognise a
  site's block page, or its text becomes a card: a 200 that `_blocked` matches is treated as a refusal.
- Perso notes are the user's own words: enrichment must never rewrite `content` for `kind = 'note'`, and the Notion
  copy and the export put the note text first, in full.

## Deployment

Railway (Dockerfile, one service: API + worker + MCP + frontend), Supabase (Postgres + Storage). Variables: see
`.env.example`. To diagnose a failed item: `debug-item` skill. To support a new source: `add-source` skill.
