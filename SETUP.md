# Setup

**English** · [Français](SETUP.fr.md)

Plan for about an hour. At the end you have the app at `https://kb.example.com` (iPhone and Mac), the Share button, the Claude connector and, if you want it, a live copy in Notion.

## 0. What you need

- The repository on your GitHub, public or private: it holds no data. `git init && git add . && git commit -m "KB" && gh repo create kb --public --source . --push` (or `--private`). Never commit your `.env`: it's in `.gitignore`.
- On your Mac: `git`, and optionally Docker Desktop to test locally.
- Run `openssl rand -hex 32` twice: the first value is your `KB_API_TOKEN`, the second your `KB_MCP_SECRET`. Keep them in your password manager.

## 1. API keys

| Service | Where | Variable | Note |
|---|---|---|---|
| Claude | [platform.claude.com](https://platform.claude.com) → API Keys | `ANTHROPIC_API_KEY` | Add $10–20 of credits. |
| Voyage AI | [dashboard.voyageai.com](https://dashboard.voyageai.com) → API Keys | `VOYAGE_API_KEY` | Add a payment method to lift the rate limits. Then **Organization → Terms of Service** and opt out of data use for training (needs the payment method and admin rights; it's a one-way switch). |
| X | X developer console → create an app → Keys and tokens | `X_BEARER_TOKEN` | Prepay $10 of credits (pay-per-use, $0.005 per post read). |
| Groq | [console.groq.com](https://console.groq.com) → API Keys | `TRANSCRIPTION_API_KEY` | For audio and video. OpenAI works too (see `.env.example`). |
| Notion (optional) | see step 7 | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` | Live copy of the KB in Notion. |

Set a monthly spending limit in each console (Anthropic, Voyage, Groq). On X, prepaid credits already act as a cap.

## 2. Supabase (database and files)

1. Create a project on [supabase.com](https://supabase.com) in the region closest to you, with a strong database password.
2. **SQL Editor** → New query → paste the whole `supabase/migrations/20261002000000_init.sql` file → **Run**. With the CLI: `supabase link`, then `supabase db push`. Updates need nothing more: the app applies this file again every time it starts (it's safe to re-run), so a new column is in place before the new code uses it. `/api/health` shows `"schema": "ok"`; set `AUTO_MIGRATE=false` to do it by hand instead.
3. In **Storage**, check that the private `kb-files` bucket exists.
4. **Connect** button → **Session pooler** tab → copy the URI into `DATABASE_URL` (replace `[YOUR-PASSWORD]`). Use the *Session* pooler (port 5432), not the *Transaction* pooler (6543).
5. **Project Settings → API Keys**: copy the *secret* key (`sb_secret_…`, or the legacy `service_role` key) into `SUPABASE_SERVICE_KEY`, and the project URL into `SUPABASE_URL`.

Security: every table has row-level security on and no grants for the public roles, so nothing is readable through Supabase's public REST API. Only your backend, connected straight to Postgres, can read it.

Note: on the free plan, Supabase pauses projects that stay inactive for a week. The worker queries the database continuously, but if you ever see a pause, the Pro plan avoids it.

## 3. Railway (API, worker, connector and app)

1. [railway.com](https://railway.com) → **New Project → Deploy from GitHub repo** → pick `kb`. The `Dockerfile` is picked up through `railway.json`.
2. **Variables → Raw Editor**: paste `.env.example` filled in with your values.
3. **Settings → Networking → Generate Domain**. Open `https://<…>.up.railway.app/api/health`: you should get `{"ok": true, …, "storage": "ok"}`. Any other `storage` value (Supabase's answer) means file shares will fail: check `SUPABASE_URL` (`https://<ref>.supabase.co`), `SUPABASE_SERVICE_KEY` (the **secret** `sb_secret_…` key, not the publishable one) and the `kb-files` bucket.
4. Custom domain: **Custom Domain** → `kb.example.com`. Add the **CNAME** `kb` → the target Railway shows, plus the verification TXT record Railway asks for, **where your DNS is managed**: at your registrar (e.g. Namecheap → Advanced DNS), or, if your domain's nameservers point to another host such as Netlify, in that host's DNS settings (Netlify → Domains → your domain → DNS settings → Add new record). Records added at the registrar are ignored when the nameservers point elsewhere.
5. Set `PUBLIC_BASE_URL=https://kb.example.com` in the variables. Railway redeploys on its own.

The service logs (**Deployments** tab) show every processed item: `Traitement …`, then `Prêt … en 12.3s`.

## 4. Install the app

- **iPhone**: Safari → `https://kb.example.com` → Share → **Add to Home Screen**. Open the app and paste your `KB_API_TOKEN`.
- **Mac**: Safari → **File → Add to Dock**, or Chrome → the install icon in the address bar.

## 5. The Share button

See **[SHORTCUT.md](SHORTCUT.md)**.

## 6. The Claude connector

1. On claude.ai: **Settings → Connectors → Add custom connector**.
   - Name: `KB`
   - URL: `https://kb.example.com/mcp/<KB_MCP_SECRET>`
2. In a conversation: **+ → Connectors → KB** (on). The connector is also available in the Claude apps.
3. Try:
   - "Search my KB for what I saved about evaluating agents."
   - "I'm building an agent that triages my email: use find_for_project and draft a plan."
   - "Add this link to my KB: https://…"

**Claude Code** (same server, header auth):

```bash
claude mcp add --transport http kb https://kb.example.com/mcp --header "Authorization: Bearer <KB_MCP_SECRET>"
```

Or simpler: add `export KB_URL=https://kb.example.com` and `export KB_MCP_SECRET=…` to your `~/.zshrc`. The repo's `.mcp.json` then connects your KB whenever you open Claude Code in this folder.

Tools exposed: `search_kb`, `get_item`, `find_for_project`, `get_principles`, `get_digest`, `get_interests`, `add_to_kb`, `list_recent`, `get_related`, `list_actions`, `resurface`, `browse_kb`, `kb_overview`.

For advice in Claude: "Use get_principles with my situation: I've been offered a better-paid job with a lot of travel. Should I take it?" Claude reads your principles and values in full, then your related notes, and answers from them.

A weekly digest with no extra code: create a scheduled task in Claude, e.g. "Every Sunday at 9am, use the KB connector to give me a digest: what I saved this week (list_recent), 3 items to rediscover (resurface) and open to-dos (list_actions)."

The connector URL contains a secret, so treat it like a password. To revoke it, change `KB_MCP_SECRET` in Railway.

## 7. Notion copy (optional, recommended)

Every item gets a page in a Notion database, rewritten whenever it changes: a second copy of your KB outside Supabase, readable everywhere, and an export that's already done.

1. In Notion, create an empty page, e.g. `KB` (it stays private to you).
2. [notion.so/profile/integrations](https://www.notion.so/profile/integrations) (it opens **Developer tools → Connections**) → **New connection** → name `KB`, your workspace, auth type **API token** (not OAuth). In the connection, **Configuration** tab: copy the **API token** (`ntn_…`) into `NOTION_TOKEN`, and keep the default capabilities (read, update, insert content). Use a connection rather than a personal access token: those expire (after a year at most) and see your whole workspace.
3. Give the connection your page: **Content access** tab → **Edit access** → tick `KB`. Or, in the page, **•••** menu (top right) → **Connections** → **+ Add connection** → `KB`. A new connection sees nothing until then; afterwards it only sees this page and what it creates inside it.
4. In the page, **•••** → **Copy link**, and paste the link into `NOTION_PARENT_PAGE_ID` (the full link works, so does the 32-character ID).
5. Optional: `NOTION_SPACES=perso` to copy only the Perso space.
6. Save the variables: Railway redeploys. Within a minute, a **Knowledge base** database appears in the page, then fills up (about 3 items per second). In the app, **Réglages → Copie dans Notion** shows the progress, the last error if any, and a **Synchroniser maintenant** button.

Good to know: the copy goes one way, from the app to Notion. Edit in the app; changes made in Notion are overwritten the next time the item changes. Deleting an item in the app moves its page to Notion's trash. If you delete the database, it's recreated and refilled. In Notion, add views: filter `Espace` = `Perso` and group by `Catégorie`, for example.

Language: **Settings → Copy in Notion → Copy language** (French or English). Switching creates a new database with its columns, cards and headings in that language and copies everything into it; the old database stays in Notion until you delete it. The Markdown export has the same choice (**Export language**).

## 8. The tech digest (agent)

Every morning at `DIGEST_HOUR`, the worker reads Hacker News, the Hugging Face papers of the day, the GitHub repos that are taking off, a set of lab and engineer blogs, and the recent posts of the people you follow on X. Claude keeps what matters for you and writes the digest, from the most general to the most technical. On Mondays it also writes the week in review and 4–5 projects for the week.

1. In Railway, keep `DIGEST_ENABLED=true` (and adjust `DIGEST_HOUR`, `DIGEST_TIMEZONE` if needed).
2. In the app, **Digest → Mes intérêts**:
   - write a few sentences about what you want to follow and at what level;
   - add the engineers you like by their X handle (their posts land in « Tes ingénieurs »), and blogs by URL (the RSS feed is found automatically);
   - accept or ignore the engineers the agent suggests. People whose tweets you save twice are followed automatically.
3. **Digest → Générer maintenant** to get the first one right away (about a minute). Then it arrives on its own every morning.
4. Vote on entries and projects (thumbs, **Garder**, **Je le fais**): the profile is recomputed every Monday from your saves, your Perso goals and these votes. **Je le fais** saves the project as a note tagged `projet`.
5. Optional, by e-mail: fill `SMTP_*` and `DIGEST_EMAIL_TO`. With Google Workspace or Gmail: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, your address in `SMTP_USER`, and an [app password](https://myaccount.google.com/apppasswords) (needs 2-step verification) in `SMTP_PASSWORD`.

Cost: about $0.10–0.20 of Claude API per day, plus $0.005 per X post read (at most `DIGEST_X_MAX_POSTS` a day; set it to 0 to skip X). In Claude, `get_digest` reads the latest digest, so you can discuss it on your plan.

## 9. Backups: don't lose anything

- **The Supabase free plan has no backups.** If the project is deleted or a bad manipulation wipes a table, nothing can be restored. Once your Perso space matters to you, switch to **Pro** ($25/month): daily backups kept 7 days (point-in-time recovery is an add-on).
- **Database backups don't include files** (PDFs, images, audio in Storage). The **Avec les fichiers d'origine** export in **Réglages** does: download it now and then and keep it in iCloud Drive.
- **The Notion copy** (step 7) is a continuous second copy of the text: your notes in full, summaries, sources.
- The plain export (**Télécharger l'export**) is Markdown: it opens in Obsidian and imports into Notion (**Import → Text & Markdown**).

## 10. Start your Perso space

1. In the app, **Perso → Nouvelle note → Principe**. Write 5 to 10 principles, one per note, the way you'd say them to yourself ("Before any big decision, I wait 24 hours and talk to someone I trust."). Then your **values**. Pin the most important ones: they come first in advice.
2. Then, whenever you like: lessons learned, goals, habits, journal, quotes. Without a category, Claude picks one.
3. From the Share button, add `#perso` to your note (and a category: `#leçon`, `#principe`, `#objectif`…) to file a link, a video or a book in Perso. Any item can also be moved between Veille and Perso from its card.
4. **Demander → Conseil**: describe the situation or the decision. The answer cites your notes; tap a number to see which one.

## 11. Check that everything works

1. Share a tweet with the Shortcut: a notification appears.
2. In the app, the card goes from "processing" to a full card within 10–60 seconds.
3. Ask a question in the chat: the answer cites `[1]`, and the source links back to the original tweet.
4. Write a principle in Perso, then ask something in **Conseil**: the principle shows up as source `[1]`.
5. If Notion is set up: the item appears in the Notion database within a minute.
6. **Digest → Générer maintenant**: a digest appears within a minute or two, with links to every source.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The Shortcut shows a 401 error | The token doesn't match `KB_API_TOKEN`. |
| Item failed with "X_BEARER_TOKEN invalide" or "Crédits X API épuisés (402)" | Wrong X key or credits to top up. Then hit **Retraiter** (reprocess). |
| YouTube video without a transcript | YouTube is blocking Railway's IP: set `YOUTUBE_PROXY_URL` (residential proxy). |
| "Fichier trop volumineux" (file too large) | Over `MAX_UPLOAD_MB`: share the link instead of the file. |
| LinkedIn or Instagram: nearly empty card | Content behind a login: share a screenshot. |
| The Claude connector doesn't answer | Test: `curl -X POST https://kb.example.com/mcp/<secret> -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` |
| Search is empty although the item exists | The item isn't "ready" yet, or `VOYAGE_API_KEY` is missing (check the logs). |
| Notion: "page parente introuvable" | The page isn't connected to the integration (step 7.2), or `NOTION_PARENT_PAGE_ID` points to another page. |
| No digest in the morning | `DIGEST_ENABLED=true`? The logs show `Digest activé` at startup and `Digest … prêt` each morning. **Régénérer** shows the error if there is one. |
| « Tes ingénieurs » stays empty | Followed people need an X handle, `X_BEARER_TOKEN` and `DIGEST_X_MAX_POSTS` > 0, and X credits. |
| Notion: nothing appears | Check **Réglages → Copie dans Notion**: it lists the missing variable or the last error. |

To rebuild a card after changing a setting, use the **Retraiter** (reprocess) button on the card.
