-- PsychDeep — Supabase hardening, run AFTER the first successful deploy.
--
-- IMPORTANT: this script targets the schema the backend actually writes to,
-- which is NOT necessarily `public`.
--
-- The backend's schema is whatever DATABASE_URL / DATABASE_SCHEMA selects:
--
--   ...?options=-csearch_path%3Dpsychdeep_v12    -> psychdeep_v12
--   DATABASE_SCHEMA=psychdeep_v12                -> psychdeep_v12
--   neither set                                  -> public
--
-- Running it against the wrong schema silently reports success on an empty
-- schema. The verification query at the bottom scans EVERY schema so that
-- can't happen unnoticed.
--
-- >>> Set TARGET_SCHEMA on the next line to match your DATABASE_URL. <<<

do $$
declare
  target_schema constant text := 'psychdeep_v12';   -- <<< EDIT THIS
  t record;
  n int := 0;
begin
  if not exists (select 1 from pg_namespace where nspname = target_schema) then
    raise exception 'Schema % does not exist. Check DATABASE_URL/DATABASE_SCHEMA.', target_schema;
  end if;

  -- Enable row level security on every table in the schema. With RLS on and
  -- no policies defined, the PostgREST roles are denied by default, while
  -- the direct Postgres connection the backend uses (and service_role)
  -- bypasses RLS and keeps working. PsychDeep enforces all of its own
  -- authorisation in FastAPI, so no policies are needed here.
  for t in
    select c.relname
    from pg_class c
    join pg_namespace ns on ns.oid = c.relnamespace
    where ns.nspname = target_schema
      and c.relkind = 'r'
      and not c.relrowsecurity
  loop
    execute format('alter table %I.%I enable row level security', target_schema, t.relname);
    raise notice 'RLS enabled on %.%', target_schema, t.relname;
    n := n + 1;
  end loop;

  -- Keep the PostgREST roles out of the schema entirely. A non-`public`
  -- schema is not exposed by PostgREST by default, so this is belt and
  -- braces rather than the primary control.
  execute format('revoke all on schema %I from anon, authenticated', target_schema);
  execute format('revoke all on all tables in schema %I from anon, authenticated', target_schema);
  execute format('revoke all on all sequences in schema %I from anon, authenticated', target_schema);
  execute format('alter default privileges in schema %I revoke all on tables from anon, authenticated', target_schema);
  execute format('alter default privileges in schema %I revoke all on sequences from anon, authenticated', target_schema);

  raise notice 'Done. RLS newly enabled on % table(s) in %.', n, target_schema;
end
$$;

-- Verification — scans every schema, not just the one above, so a script
-- pointed at the wrong schema cannot report a false all-clear.
--
-- Expect: every application table shows rls_enabled = true and both
-- *_can_select columns false. A schema you don't recognise holding your
-- tables means DATABASE_URL is pointing somewhere unexpected.
select n.nspname                                       as schema,
       c.relname                                       as table_name,
       c.relrowsecurity                                as rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT')          as anon_can_select,
       has_table_privilege('authenticated', c.oid, 'SELECT') as auth_can_select
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r'
  and n.nspname not like 'pg\_%'
  and n.nspname not in ('information_schema', 'auth', 'storage', 'realtime',
                        'vault', 'extensions', 'graphql', 'graphql_public',
                        'supabase_migrations')
order by n.nspname, c.relname;
