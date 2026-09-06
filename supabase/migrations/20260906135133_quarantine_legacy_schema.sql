begin;

-- The pre-v12 prototype schema is not part of the current application model.
-- Keep its August 2026 data recoverable, but remove it from the operational
-- namespace and from every application/API role.
do $$
begin
    if exists (select 1 from pg_namespace where nspname = 'psychdeep')
       and not exists (select 1 from pg_namespace where nspname = 'psychdeep_legacy_20260809') then
        alter schema psychdeep rename to psychdeep_legacy_20260809;
    end if;
end
$$;

do $$
declare
    role_name text;
begin
    if not exists (select 1 from pg_namespace where nspname = 'psychdeep_legacy_20260809') then
        return;
    end if;

    revoke all on schema psychdeep_legacy_20260809 from public;

    foreach role_name in array array['anon','authenticated','service_role','psychdeep_backend'] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('revoke all on schema psychdeep_legacy_20260809 from %I', role_name);
            execute format('revoke all privileges on all tables in schema psychdeep_legacy_20260809 from %I', role_name);
            execute format('revoke all privileges on all sequences in schema psychdeep_legacy_20260809 from %I', role_name);
            execute format('revoke all privileges on all functions in schema psychdeep_legacy_20260809 from %I', role_name);
        end if;
    end loop;
end
$$;

commit;
