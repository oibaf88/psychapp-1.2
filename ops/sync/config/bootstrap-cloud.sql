-- Preflight only. The Supabase migrations create and harden psychdeep_sync and
-- psychdeep_sync (role). This file intentionally does not create credentials,
-- alter LOGIN state, or grant owner privileges.

do $$
declare
    policy_count integer;
begin
    if not exists (select 1 from pg_namespace where nspname = 'psychdeep_sync') then
        raise exception 'psychdeep_sync schema missing: apply Supabase migrations first';
    end if;

    if not exists (select 1 from pg_roles where rolname = 'psychdeep_sync') then
        raise exception 'psychdeep_sync role missing: apply Supabase migrations first';
    end if;

    if not has_schema_privilege('psychdeep_sync', 'psychdeep_sync', 'USAGE')
       or not has_schema_privilege('psychdeep_sync', 'psychdeep_sync', 'CREATE')
       or not has_schema_privilege('psychdeep_sync', 'psychdeep_v12', 'USAGE') then
        raise exception 'psychdeep_sync schema privileges are incomplete';
    end if;

    if has_table_privilege('psychdeep_sync', 'psychdeep_v12.llm_endpoint_configs', 'SELECT')
       or has_table_privilege('psychdeep_sync', 'psychdeep_v12.password_reset_tokens', 'SELECT') then
        raise exception 'sync role can read an excluded runtime/auth table';
    end if;

    select count(*) into policy_count
    from pg_policies
    where schemaname = 'psychdeep_v12'
      and policyname = 'sync_replication_access';

    if policy_count <> 19 then
        raise exception 'expected 19 sync policies, found %', policy_count;
    end if;
end
$$;

select
    r.rolcanlogin as login_enabled,
    r.rolinherit as inherits_roles,
    r.rolconnlimit as connection_limit,
    has_schema_privilege('psychdeep_sync', 'psychdeep_sync', 'CREATE') as can_create_runtime_tables
from pg_roles r
where r.rolname = 'psychdeep_sync';
