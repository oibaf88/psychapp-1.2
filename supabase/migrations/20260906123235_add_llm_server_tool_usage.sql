-- Add provider-reported server-tool counters to the LLM usage ledger.

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

alter table psychdeep_v12.llm_usage_events
    add column if not exists web_search_requests integer,
    add column if not exists web_fetch_requests integer;

alter table psychdeep_v12.llm_usage_events
    add constraint ck_llm_usage_server_tools_nonnegative
    check (
        (web_search_requests is null or web_search_requests >= 0)
        and (web_fetch_requests is null or web_fetch_requests >= 0)
    );

reset role;
revoke psychdeep_backend from postgres granted by postgres;

commit;
