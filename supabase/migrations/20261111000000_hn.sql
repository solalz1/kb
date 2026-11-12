-- The Hacker News space (app/hn.py): the day's best stories, kept apart from what the user saves.
-- Idempotent and safe to re-run at every start: nothing here changes a row.

-- items.space accepts 'hn'. The check is rebuilt only while it doesn't know 'hn' yet, so a restart neither locks
-- nor scans the table.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.items'::regclass and conname = 'items_space_check'
      and pg_get_constraintdef(oid) like '%''hn''%'
  ) then
    alter table public.items drop constraint if exists items_space_check;
    alter table public.items add constraint items_space_check check (space in ('main', 'perso', 'hn'));
  end if;
end $$;

-- the daily run skips a story already fetched, by its HN id
create index if not exists items_hn_id on public.items ((metadata->'hn'->>'id')) where space = 'hn';
