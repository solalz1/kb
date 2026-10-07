-- =============================================================================
-- KB — schéma initial
-- Postgres 15+ / Supabase. Idempotent : peut être rejoué sans casse.
-- =============================================================================

create schema if not exists extensions;
create extension if not exists vector   with schema extensions;
create extension if not exists unaccent with schema extensions;

set search_path = public, extensions;

-- -----------------------------------------------------------------------------
-- Recherche plein texte bilingue FR/EN
--   1. stopwords français (mot non reconnu => passé au suivant)
--   2. suppression des accents
--   3. stopwords anglais + minuscules
-- -----------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_ts_dict where dictname = 'kb_stop_fr') then
    create text search dictionary public.kb_stop_fr (template = pg_catalog.simple, stopwords = french, accept = false);
  end if;
  if not exists (select 1 from pg_ts_dict where dictname = 'kb_stop_en') then
    create text search dictionary public.kb_stop_en (template = pg_catalog.simple, stopwords = english);
  end if;
  if not exists (select 1 from pg_ts_config where cfgname = 'kb') then
    create text search configuration public.kb (copy = pg_catalog.simple);
    alter text search configuration public.kb
      alter mapping for asciiword, word, numword, asciihword, hword, numhword,
                        hword_asciipart, hword_part, hword_numpart
      with public.kb_stop_fr, extensions.unaccent, public.kb_stop_en;
  end if;
end $$;

-- Requête "OU" à partir d'une phrase en langage naturel
-- ("outils pour agents RAG" -> 'outil' | 'agent' | 'rag')
create or replace function public.kb_or_query(q text)
returns tsquery
language sql immutable
as $$
  select nullif(replace(plainto_tsquery('public.kb', coalesce(q, ''))::text, ' & ', ' | '), '')::tsquery
$$;

-- -----------------------------------------------------------------------------
-- Items : un élément capturé (tweet, article, vidéo, PDF, image, note…)
-- -----------------------------------------------------------------------------
create table if not exists public.items (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),

  -- file de traitement
  status          text not null default 'pending'
                  check (status in ('pending', 'processing', 'ready', 'error')),
  attempts        int  not null default 0,
  next_attempt_at timestamptz,
  locked_at       timestamptz,
  error           text,

  -- ce qui a été partagé
  input_url       text,
  input_text      text,
  file_path       text,          -- chemin dans le bucket Storage
  file_name       text,
  file_mime       text,
  user_note       text,          -- « pourquoi je garde ça »

  -- provenance (toujours citée)
  kind            text,          -- tweet | article | youtube | video | audio | pdf | image | document | note | repo | paper
  source_url      text,          -- URL canonique (ex. https://x.com/handle/status/id)
  title           text,
  author          text,
  author_url      text,
  site_name       text,
  published_at    timestamptz,
  language        text,
  thumbnail_url   text,

  -- contenu + enrichissement
  content         text,
  summary         text,
  key_points      jsonb  not null default '[]'::jsonb,
  tags            text[] not null default '{}',
  entities        jsonb  not null default '[]'::jsonb,
  use_cases       jsonb  not null default '[]'::jsonb,
  genre           text,
  metadata        jsonb  not null default '{}'::jsonb,

  -- rangement : 'main' = veille (ce que tu captures), 'perso' = développement personnel
  space           text not null default 'main' check (space in ('main', 'perso')),
  category        text,          -- catégorie perso : principe, valeur, lecon, objectif, habitude…

  -- usage
  pinned          boolean not null default false,
  archived        boolean not null default false,
  view_count      int not null default 0,
  last_viewed_at  timestamptz,

  -- copie Notion (facultative) : notion_synced_at = null => à (re)copier
  notion_page_id   text,
  notion_synced_at timestamptz
);

-- Mise à niveau d'une base créée avec une version antérieure de ce fichier
alter table public.items add column if not exists space text not null default 'main' check (space in ('main', 'perso'));
alter table public.items add column if not exists category text;
alter table public.items add column if not exists notion_page_id text;
alter table public.items add column if not exists notion_synced_at timestamptz;
-- the card (title, summary, key points, use cases) in the second language: {"en": {...}}
alter table public.items add column if not exists translations jsonb not null default '{}'::jsonb;
-- journal: the day a Perso note of category 'journal' belongs to (any day, chosen in the calendar)
alter table public.items add column if not exists entry_date date;

create index if not exists items_created_idx  on public.items (created_at desc);
create index if not exists items_space_idx    on public.items (space, category, created_at desc);
create index if not exists items_notion_idx   on public.items (created_at) where notion_synced_at is null;
create index if not exists items_status_idx   on public.items (status, next_attempt_at, created_at);
create index if not exists items_source_idx   on public.items (source_url);
create index if not exists items_kind_idx     on public.items (kind);
create index if not exists items_tags_idx     on public.items using gin (tags);
create index if not exists items_entities_idx on public.items using gin (entities jsonb_path_ops);

-- -----------------------------------------------------------------------------
-- Chunks : morceaux de contenu embeddés. chunk_index = -1 => « fiche résumé ».
-- -----------------------------------------------------------------------------
create table if not exists public.chunks (
  id          bigserial primary key,
  item_id     uuid not null references public.items (id) on delete cascade,
  chunk_index int  not null,
  content     text not null,
  embedding   extensions.vector(1024),
  fts         tsvector generated always as (to_tsvector('public.kb', content)) stored
);

create index if not exists chunks_item_idx      on public.chunks (item_id, chunk_index);
create index if not exists chunks_fts_idx       on public.chunks using gin (fts);
create index if not exists chunks_embedding_idx on public.chunks using hnsw (embedding extensions.vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- Liens automatiques entre items
-- -----------------------------------------------------------------------------
create table if not exists public.item_links (
  source_id  uuid not null references public.items (id) on delete cascade,
  target_id  uuid not null references public.items (id) on delete cascade,
  similarity real,
  reason     text,
  created_at timestamptz not null default now(),
  primary key (source_id, target_id),
  check (source_id <> target_id)
);
create index if not exists item_links_target_idx on public.item_links (target_id);

-- -----------------------------------------------------------------------------
-- Actions extraites (outil à tester, papier à lire, compte à suivre…)
-- -----------------------------------------------------------------------------
create table if not exists public.actions (
  id         bigserial primary key,
  item_id    uuid not null references public.items (id) on delete cascade,
  text       text not null,
  kind       text,
  done       boolean not null default false,
  done_at    timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists actions_open_idx on public.actions (done, created_at desc);

-- -----------------------------------------------------------------------------
-- Réglages internes (ex. identifiants de la base Notion créée par l'app)
-- et pages Notion à mettre à la corbeille après une suppression
-- -----------------------------------------------------------------------------
create table if not exists public.kb_settings (
  key        text primary key,
  value      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.notion_trash (
  page_id    text primary key,
  created_at timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- Agent de veille : sources suivies (ingénieurs, blogs), digests quotidiens/hebdo, retours
-- -----------------------------------------------------------------------------
create table if not exists public.watch (
  id         bigserial primary key,
  kind       text not null check (kind in ('person', 'feed')),
  name       text not null,
  x_handle   text,                 -- compte X, sans @
  url        text,                 -- site ou page de la personne
  feed_url   text,                 -- flux RSS/Atom
  origin     text not null default 'manual' check (origin in ('manual', 'auto', 'default', 'suggested', 'x_follow')),
  status     text not null default 'active' check (status in ('active', 'suggested', 'muted')),
  note       text,                 -- pourquoi la suivre
  last_ok_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);
-- x_follow: followed on X by the user (digest/following.py). Re-created so older databases accept it.
alter table public.watch drop constraint if exists watch_origin_check;
alter table public.watch add constraint watch_origin_check
  check (origin in ('manual', 'auto', 'default', 'suggested', 'x_follow'));
create unique index if not exists watch_handle_idx on public.watch (lower(x_handle)) where x_handle is not null;
create unique index if not exists watch_feed_idx   on public.watch (feed_url) where feed_url is not null;

create table if not exists public.digests (
  id           bigserial primary key,
  kind         text not null check (kind in ('daily', 'weekly')),
  period_start date not null,
  period_end   date not null,
  status       text not null default 'generating' check (status in ('generating', 'ready', 'error')),
  attempts     int  not null default 1,
  headline     text,
  content      text,                                  -- Markdown (e-mail, connecteur, export)
  data         jsonb not null default '{}'::jsonb,    -- sections, entrées, projets (pour l'app)
  model        text,
  error        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (kind, period_start)
);
create index if not exists digests_recent_idx on public.digests (created_at desc);

create table if not exists public.digest_feedback (
  id         bigserial primary key,
  digest_id  bigint not null references public.digests (id) on delete cascade,
  target     text not null check (target in ('entry', 'project')),
  entry_key  text not null,
  vote       smallint not null,   -- 1 intéressant, -1 pas pour moi, 2 gardé dans la KB / « je le fais »
  title      text,
  item_id    uuid references public.items (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (digest_id, target, entry_key)
);

-- -----------------------------------------------------------------------------
-- updated_at automatique ; toute modification du contenu rend la copie Notion obsolète
-- -----------------------------------------------------------------------------
create or replace function public.kb_touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  if new.notion_synced_at is not distinct from old.notion_synced_at
     and (new.title, new.summary, new.content, new.input_text, new.user_note, new.tags, new.key_points,
          new.use_cases, new.entities, new.space, new.category, new.status, new.archived, new.kind,
          new.source_url, new.author, new.published_at)
         is distinct from
         (old.title, old.summary, old.content, old.input_text, old.user_note, old.tags, old.key_points,
          old.use_cases, old.entities, old.space, old.category, old.status, old.archived, old.kind,
          old.source_url, old.author, old.published_at) then
    new.notion_synced_at = null;
  end if;
  return new;
end $$;

drop trigger if exists items_touch on public.items;
create trigger items_touch before update on public.items
for each row execute function public.kb_touch_updated_at();

-- -----------------------------------------------------------------------------
-- File d'attente : réserve le prochain item à traiter (sûr en concurrence).
-- Un item resté « processing » trop longtemps (process redémarré, crash) est repris,
-- sauf s'il a déjà épuisé ses essais : il passe alors en erreur au lieu de boucler.
-- -----------------------------------------------------------------------------
create or replace function public.claim_next_item(
  stale_after  interval default interval '45 minutes',
  max_attempts int      default 3
)
returns setof public.items
language sql
as $$
  update public.items
     set status = 'error', locked_at = null,
         error = coalesce(error || ' — ', '') || 'Traitement interrompu à plusieurs reprises (redémarrage ou délai dépassé).'
   where status = 'processing' and locked_at < now() - stale_after and attempts >= max_attempts;

  update public.items
     set status = 'processing', locked_at = now(), attempts = attempts + 1
   where id = (
     select id from public.items
      where (status = 'pending' and (next_attempt_at is null or next_attempt_at <= now()))
         or (status = 'processing' and locked_at < now() - stale_after)
      order by created_at
      for update skip locked
      limit 1
   )
  returning *;
$$;

-- -----------------------------------------------------------------------------
-- Recherche hybride (vectorielle + plein texte) fusionnée par RRF
-- -----------------------------------------------------------------------------
drop function if exists public.hybrid_search(text, extensions.vector, int, text[], text[], boolean, float, float, int);

create or replace function public.hybrid_search(
  query_text       text,
  query_embedding  extensions.vector(1024),
  match_count      int     default 30,
  filter_kinds     text[]  default null,
  filter_tags      text[]  default null,
  include_archived boolean default false,
  full_text_weight float   default 1.0,
  semantic_weight  float   default 1.0,
  rrf_k            int     default 50,
  filter_spaces    text[]  default null
)
returns table (
  chunk_id    bigint,
  item_id     uuid,
  chunk_index int,
  content     text,
  similarity  float,
  score       float
)
language sql stable
set search_path = public, extensions
as $$
  with eligible as (
    select c.id, c.item_id, c.chunk_index, c.content, c.embedding, c.fts
      from public.chunks c
      join public.items i on i.id = c.item_id
     where i.status = 'ready'
       and (include_archived or not i.archived)
       and (filter_kinds is null or i.kind = any (filter_kinds))
       and (filter_tags  is null or i.tags && filter_tags)
       and (filter_spaces is null or i.space = any (filter_spaces))
  ),
  semantic as (
    select e.id,
           1 - (e.embedding <=> query_embedding) as similarity,
           row_number() over (order by e.embedding <=> query_embedding) as rank_ix
      from eligible e
     where e.embedding is not null
     order by e.embedding <=> query_embedding
     limit match_count * 3
  ),
  keyword as (
    select e.id,
           row_number() over (order by ts_rank_cd(e.fts, q) desc) as rank_ix
      from eligible e, public.kb_or_query(query_text) q
     where q is not null and e.fts @@ q
     order by ts_rank_cd(e.fts, q) desc
     limit match_count * 3
  ),
  fused as (
    select coalesce(s.id, k.id) as id,
           s.similarity,
           coalesce(semantic_weight  / (rrf_k + s.rank_ix), 0.0)
         + coalesce(full_text_weight / (rrf_k + k.rank_ix), 0.0) as score
      from semantic s
      full outer join keyword k on k.id = s.id
  )
  select e.id, e.item_id, e.chunk_index, e.content, f.similarity, f.score
    from fused f
    join eligible e on e.id = f.id
   order by f.score desc
   limit match_count;
$$;

-- -----------------------------------------------------------------------------
-- Items proches (via les fiches résumé)
-- -----------------------------------------------------------------------------
create or replace function public.similar_items(
  target         uuid,
  match_count    int   default 8,
  min_similarity float default 0.0
)
returns table (item_id uuid, similarity float)
language sql stable
set search_path = public, extensions
as $$
  select c2.item_id, 1 - (c2.embedding <=> c1.embedding) as similarity
    from public.chunks c1
    join public.chunks c2 on c2.chunk_index = -1 and c2.item_id <> c1.item_id
    join public.items  i  on i.id = c2.item_id and i.status = 'ready' and not i.archived
   where c1.item_id = target and c1.chunk_index = -1
     and 1 - (c2.embedding <=> c1.embedding) >= min_similarity
   order by c2.embedding <=> c1.embedding
   limit match_count;
$$;

-- -----------------------------------------------------------------------------
-- Sécurité Supabase : aucune table n'est exposée via l'API REST publique.
-- Le backend se connecte en direct (rôle postgres) et n'est pas concerné.
-- -----------------------------------------------------------------------------
alter table public.items      enable row level security;
alter table public.chunks     enable row level security;
alter table public.item_links enable row level security;
alter table public.actions    enable row level security;
alter table public.kb_settings  enable row level security;
alter table public.notion_trash enable row level security;
alter table public.watch           enable row level security;
alter table public.digests         enable row level security;
alter table public.digest_feedback enable row level security;

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables    in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke execute on function public.claim_next_item(interval, int) from %I', r);
      execute format('revoke execute on function public.hybrid_search(text, extensions.vector, int, text[], text[], boolean, float, float, int, text[]) from %I', r);
      execute format('revoke execute on function public.similar_items(uuid, int, float) from %I', r);
    end if;
  end loop;
end $$;

revoke execute on function public.claim_next_item(interval, int) from public;
revoke execute on function public.hybrid_search(text, extensions.vector, int, text[], text[], boolean, float, float, int, text[]) from public;
revoke execute on function public.similar_items(uuid, int, float) from public;

-- -----------------------------------------------------------------------------
-- Bucket Storage privé pour les fichiers (PDF, images, audio…)
-- -----------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.schemata where schema_name = 'storage') then
    insert into storage.buckets (id, name, public)
    values ('kb-files', 'kb-files', false)
    on conflict (id) do nothing;
  end if;
end $$;
