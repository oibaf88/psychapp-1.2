-- PsychDeep 1.2: drop unused mobile telemetry tables.
--
-- biometric_data and app_usage_data were ingested from a mobile path that
-- never shipped. They are empty in production. This migration refuses to
-- run if any row appeared between the last check and the DROP, so it cannot
-- silently destroy data.
--
-- Same role dance as the rest of this folder.

begin;

do $$
declare
    membership_is_expected boolean;
    biometric_rows bigint;
    app_usage_rows bigint;
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

    if to_regclass('psychdeep_v12.biometric_data') is not null then
        execute 'select count(*) from psychdeep_v12.biometric_data' into biometric_rows;
        if biometric_rows > 0 then
            raise exception 'biometric_data is not empty (% rows); drop aborted', biometric_rows;
        end if;
    end if;
    if to_regclass('psychdeep_v12.app_usage_data') is not null then
        execute 'select count(*) from psychdeep_v12.app_usage_data' into app_usage_rows;
        if app_usage_rows > 0 then
            raise exception 'app_usage_data is not empty (% rows); drop aborted', app_usage_rows;
        end if;
    end if;
end
$$;

grant psychdeep_backend to postgres with set true;
set local role psychdeep_backend;

drop table if exists psychdeep_v12.biometric_data;
drop table if exists psychdeep_v12.app_usage_data;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

do $$
declare
    membership_is_restored boolean;
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

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if to_regclass('psychdeep_v12.biometric_data') is not null then
        raise exception 'biometric_data was not dropped';
    end if;
    if to_regclass('psychdeep_v12.app_usage_data') is not null then
        raise exception 'app_usage_data was not dropped';
    end if;
end
$$;

commit;
