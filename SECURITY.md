# Security

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead: **Security → Report a vulnerability**
on this repository. Include the steps to reproduce and the impact. You'll get an answer within a week.

## Security model

KB is designed for **one user per deployment**. Each person runs their own instance, with their own keys and data.

- **Code vs data**: this repository contains only code. Each deployment's items live in its own Supabase project
  (row-level security on every table, no grants for the `anon`/`authenticated` roles), and its keys live in the
  host's environment variables.
- **API and web app**: a single bearer token (`KB_API_TOKEN`), compared in constant time. Anyone with the token can
  read and write the whole knowledge base and spend the deployment's API credits, so generate it with
  `openssl rand -hex 32` and rotate it if in doubt.
- **MCP connector**: either the secret URL path `/mcp/<KB_MCP_SECRET>` (for Claude custom connectors) or an
  `Authorization: Bearer` header (for Claude Code). Treat the connector URL like a password.
- **Server-side fetching**: the ingest endpoint downloads URLs on the server. It's only reachable with the token;
  if you open it to other users, add SSRF protections first (block private IP ranges, cap sizes and redirects).
- **Spending**: set monthly limits in the Anthropic, Voyage and transcription consoles.
- **Nothing to find in the public repo**: no real domain, ID or key is committed (examples use `kb.example.com`);
  every real value lives in environment variables. `.env` is in `.gitignore`, and `.claude/settings.json` stops
  Claude Code from reading it.
- **No indexing**: `robots.txt`, a `noindex` meta tag and an `X-Robots-Tag: noindex` header on every response. Even if
  someone guesses the address, the app shows only the token prompt.
- **Data processors**: item content is sent to the Anthropic API (enrichment, chat), Voyage AI (embeddings; opt out
  of training in its dashboard), the transcription provider (audio/video only), the X API (reading tweets) and, if
  enabled, the user's own Notion workspace. Perso-space notes follow the same path.
- **Digest agent**: reads public sources only (Hacker News, Hugging Face, GitHub, RSS feeds, public X posts). To rank
  them, it sends Claude a summary of the user's interests built from their items (tags, people, Perso goals). Digests
  can be e-mailed through the user's own SMTP account.
- **Notion copy**: an internal integration that only sees the page it was connected to. The copy is one-way
  (app → Notion); revoking the integration in Notion stops it.
