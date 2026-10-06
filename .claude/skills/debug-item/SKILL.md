---
name: debug-item
description: Diagnose and fix a KB item that failed, is stuck "processing", or was badly extracted or summarized (tweet without its thread, video without a transcript, empty PDF…). Use when the user says a share didn't work, or pastes a URL or item id that misbehaves.
---

Item to diagnose: $ARGUMENTS

1. **Find the item** (Supabase MCP, `execute_sql`, read-only):
   `select id, status, attempts, error, kind, input_url, source_url, title, length(content) as n, metadata from items where id::text = '<id>' or input_url ilike '%<url fragment>%' or source_url ilike '%<url fragment>%' order by created_at desc limit 5;`
2. **Read the logs** of the Railway service around its processing (Railway MCP or `railway logs`): look for
   `Traitement <id>`, `Échec <id>` and the stack trace.
3. **Classify the cause**:
   - `ExtractionError` (not found, private, too long) → expected behavior; explain it to the user.
   - Network or quota error (X 401/402/429, Groq 429, YouTube "Sign in to confirm") → configuration (key, credits,
     `YOUTUBE_PROXY_URL`).
   - Unexpected Python exception → bug: reproduce it with a test in `backend/tests/` (faking the external response),
     fix it, run `pytest -q`.
   - Thin content (`metadata.thin_content`) → page behind a login: suggest sharing a screenshot.
4. **Retry**: `POST /api/items/<id>/reprocess` (or the "Retraiter" button), then check the status again.
5. Summarize for the user: cause, fix, and anything left for them to do.

Never print secrets (Railway variables, `.env`) in the answer.
