-- PsychDeep 1.2: raise the stored LLM inference timeout ceiling to 5.000 s.
--
-- Expand-only. The connection handshake stays fail-fast in application code
-- (10 s). This CHECK is the inference wait once the TCP session exists: a
-- local model loading into VRAM can take minutes, and 600 s cut it off.
--
-- Same role dance as the rest of this folder: work as psychdeep_backend,
-- hand the membership back before commit.

begin;

do $$
declare
    membership_is_expected boolean;
begin
    select count(*) = 1
       and bool_and(m.admin_option)
       and not bool_or(m.inherit_option)
       and not bool_or(m.set_option)
       and bool_and(grantor.rolname = 'supabase_admin')
      into membership_is_expected
      from pg_auth_members m
      join pg_roles granted_role on granted_role.oid = m.roleid
      join pg_roles member_role on member_role.oid = m.member
      join pg_roles grantor on grantor.oid = m.grantor
     where granted_role.rolname = 'psychdeep_backend'
       and member_role.rolname = 'postgres';

    if not coalesce(membership_is_expected, false) then
        raise exception 'Unexpected postgres -> psychdeep_backend membership; migration stopped safely';
    end if;
end
$$;

grant psychdeep_backend to postgres with set true;
set local role psychdeep_backend;

alter table psychdeep_v12.llm_endpoint_configs
    drop constraint if exists ck_llm_endpoint_timeout;

alter table psychdeep_v12.llm_endpoint_configs
    add constraint ck_llm_endpoint_timeout
        check (timeout_seconds between 5 and 5000);

reset role;
revoke psychdeep_backend from postgres granted by postgres;

do $$
declare
    membership_is_restored boolean;
    timeout_check text;
begin
    select count(*) = 1
       and bool_and(m.admin_option)
       and not bool_or(m.inherit_option)
       and not bool_or(m.set_option)
       and bool_and(grantor.rolname = 'supabase_admin')
      into membership_is_restored
      from pg_auth_members m
      join pg_roles granted_role on granted_role.oid = m.roleid
      join pg_roles member_role on member_role.oid = m.member
      join pg_roles grantor on grantor.oid = m.grantor
     where granted_role.rolname = 'psychdeep_backend'
       and member_role.rolname = 'postgres';

    select pg_get_constraintdef(c.oid)
      into timeout_check
      from pg_constraint c
      join pg_class relation on relation.oid = c.conrelid
      join pg_namespace namespace on namespace.oid = relation.relnamespace
     where namespace.nspname = 'psychdeep_v12'
       and relation.relname = 'llm_endpoint_configs'
       and c.conname = 'ck_llm_endpoint_timeout';

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if timeout_check is null or timeout_check not like '%5000%' then
        raise exception 'ck_llm_endpoint_timeout was not expanded to 5000 (found %)', timeout_check;
    end if;
end
$$;

commit;
