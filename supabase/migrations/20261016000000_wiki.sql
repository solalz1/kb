-- =============================================================================
-- KB — the wiki: pages Claude writes and keeps up to date from the items (app/wiki.py)
-- Idempotent, like every migration here: safe to run at every start.
-- =============================================================================

set search_path = public, extensions;

-- -----------------------------------------------------------------------------
-- Pages: one per theme, concept, entity or book (never one per item), in one of five sections.
-- `content` is Markdown with [[Title]] links to other pages and [src:xxxxxxxx] references to items
-- (the first 8 characters of the item's id). `links` holds the lowercased titles it links to (backlinks).
-- -----------------------------------------------------------------------------
create table if not exists public.wiki_pages (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  slug        text not null,
  section     text not null default 'research'
              check (section in ('personal', 'research', 'reading', 'business', 'competitive')),
  kind        text not null default 'theme'
              check (kind in ('theme', 'entity', 'book', 'synthesis', 'answer')),
  summary     text,                          -- one line, for the index
  content     text not null default '',
  links       text[] not null default '{}',
  revision    int  not null default 1,       -- compare-and-swap: every write says which revision it read
  updated_by  text not null default 'claude' check (updated_by in ('claude', 'user')),
  archived    boolean not null default false,
  metadata    jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  fts         tsvector generated always as (
                to_tsvector('public.kb', coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || content)) stored
);

create unique index if not exists wiki_pages_title_idx on public.wiki_pages (lower(title)) where not archived;
create unique index if not exists wiki_pages_slug_idx  on public.wiki_pages (slug) where not archived;
create index if not exists wiki_pages_section_idx on public.wiki_pages (section, updated_at desc);
create index if not exists wiki_pages_links_idx   on public.wiki_pages using gin (links);
create index if not exists wiki_pages_fts_idx     on public.wiki_pages using gin (fts);

-- The items a page draws on (a deleted item leaves the pages, its facts stay until the next review)
create table if not exists public.wiki_page_sources (
  page_id  uuid not null references public.wiki_pages (id) on delete cascade,
  item_id  uuid not null references public.items (id) on delete cascade,
  added_at timestamptz not null default now(),
  primary key (page_id, item_id)
);
create index if not exists wiki_page_sources_item_idx on public.wiki_page_sources (item_id);

-- Every version of every page: nothing a later write replaces is lost
create table if not exists public.wiki_revisions (
  id         bigserial primary key,
  page_id    uuid not null references public.wiki_pages (id) on delete cascade,
  revision   int  not null,
  title      text not null,
  section    text not null,
  summary    text,
  content    text not null,
  author     text not null check (author in ('claude', 'user')),
  reason     text,                        -- ingest, lint, answer, obsidian, chat…
  created_at timestamptz not null default now()
);
create index if not exists wiki_revisions_page_idx on public.wiki_revisions (page_id, revision desc);

-- log.md: what happened to the wiki, in order (ingests, reviews, saved answers, edits made in Obsidian)
create table if not exists public.wiki_log (
  id     bigserial primary key,
  at     timestamptz not null default now(),
  kind   text not null check (kind in ('ingest', 'lint', 'answer', 'edit', 'sync')),
  title  text not null,
  detail jsonb not null default '{}'::jsonb
);
create index if not exists wiki_log_at_idx on public.wiki_log (at desc);

-- -----------------------------------------------------------------------------
-- The wiki's queue: one job at a time (an item to integrate, an answer to file, the nightly review), so two jobs
-- never rewrite the same page at once.
-- -----------------------------------------------------------------------------
create table if not exists public.wiki_jobs (
  id              bigserial primary key,
  kind            text not null check (kind in ('ingest', 'answer', 'lint')),
  item_id         uuid references public.items (id) on delete cascade,
  payload         jsonb not null default '{}'::jsonb,
  status          text not null default 'pending' check (status in ('pending', 'running', 'done', 'error')),
  attempts        int  not null default 0,
  next_attempt_at timestamptz,
  error           text,
  result          jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  started_at      timestamptz,
  finished_at     timestamptz
);
create index if not exists wiki_jobs_queue_idx on public.wiki_jobs (status, id);
-- an item waits in the queue once
create unique index if not exists wiki_jobs_pending_item_idx on public.wiki_jobs (item_id)
  where kind = 'ingest' and status = 'pending';

-- -----------------------------------------------------------------------------
-- Takes the next job (safe when two containers run side by side). A job left 'running' too long (restart, crash)
-- goes back to the queue, or to 'error' once it has used its attempts. 60 minutes: longer than the worker's lease
-- (app/wiki.py), so a job is only taken over once its worker is surely gone. The items saved before the wiki join
-- the queue once, from the app (wiki.backfill_once), not from here.
-- -----------------------------------------------------------------------------
create or replace function public.claim_next_wiki_job(
  stale_after  interval default interval '60 minutes',
  max_attempts int      default 3
)
returns setof public.wiki_jobs
language sql
as $$
  update public.wiki_jobs
     set status = 'error', error = coalesce(error || ' — ', '') || 'Interrompu à plusieurs reprises.'
   where status = 'running' and started_at < now() - stale_after and attempts >= max_attempts;

  update public.wiki_jobs
     set status = 'running', started_at = now(), attempts = attempts + 1
   where id = (
     select id from public.wiki_jobs
      where (status = 'pending' and (next_attempt_at is null or next_attempt_at <= now()))
         or (status = 'running' and started_at < now() - stale_after)
      order by id
      for update skip locked
      limit 1
   )
  returning *;
$$;

-- -----------------------------------------------------------------------------
-- Supabase: nothing exposed through the public REST API (the backend connects directly)
-- -----------------------------------------------------------------------------
alter table public.wiki_pages        enable row level security;
alter table public.wiki_page_sources enable row level security;
alter table public.wiki_revisions    enable row level security;
alter table public.wiki_log          enable row level security;
alter table public.wiki_jobs         enable row level security;

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on public.wiki_pages, public.wiki_page_sources, public.wiki_revisions, '
                     'public.wiki_log, public.wiki_jobs from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke execute on function public.claim_next_wiki_job(interval, int) from %I', r);
    end if;
  end loop;
end $$;

revoke execute on function public.claim_next_wiki_job(interval, int) from public;
