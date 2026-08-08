-- PsychDeep — Supabase hardening, run AFTER the first successful deploy.
--
-- Why this exists
-- ---------------
-- The backend creates its tables with SQLAlchemy's create_all() on startup,
-- so they do not exist until the API has booted once. The migration
-- `lock_public_schema_from_postgrest_roles` already revoked the default
-- privileges that Supabase would otherwise grant to `anon` and
-- `authenticated` on new tables in `public`, which is the primary control.
--
-- This script is the belt-and-braces second layer: it enables row level
-- security on every table in `public`. With RLS on and no policies
-- defined, PostgREST roles are denied by default, while the direct
-- Postgres connection the backend uses (and `service_role`) bypasses RLS
-- and keeps working. PsychDeep enforces all of its own authorisation in
-- FastAPI, so no policies are needed here.
--
-- Safe to run repeatedly.
--
-- Run it from Supabase Dashboard -> SQL Editor, or:
--   psql "$DATABASE_URL" -f supabase/harden.sql

do $$
declare
  t record;
begin
  for t in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'          -- ordinary tables only
      and not c.relrowsecurity     -- skip ones already covered
  loop
    execute format('alter table public.%I enable row level security', t.relname);
    raise notice 'RLS enabled on public.%', t.relname;
  end loop;
end
$$;

-- Re-revoke explicitly, in case a table was created by a role whose own
-- default privileges still grant the PostgREST roles.
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

-- Verification: every row should show rls_enabled = true, and neither
-- anon nor authenticated should appear in any table's ACL.
select c.relname                             as table_name,
       c.relrowsecurity                      as rls_enabled,
       coalesce(
         (select count(*) from pg_policies p
          where p.schemaname = 'public' and p.tablename = c.relname), 0
       )                                     as policy_count,
       has_table_privilege('anon', c.oid, 'SELECT')          as anon_can_select,
       has_table_privilege('authenticated', c.oid, 'SELECT') as auth_can_select
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by c.relname;
