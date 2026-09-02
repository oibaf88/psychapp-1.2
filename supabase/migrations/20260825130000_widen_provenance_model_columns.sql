-- PsychDeep 1.2: make the provenance columns fit the model names the
-- configuration actually accepts.
--
-- `llm_endpoint_configs.chat_model`, `.analysis_model` and `.copilot_model`
-- are varchar(160), and LLMEndpointConfigIn validates up to 160 characters.
-- The columns that record which model answered were varchar(128).
--
-- The gap is not theoretical: an operator configures a valid 140-character
-- model identifier, the provider call succeeds, and the INSERT that records
-- the answer is rejected. On the copilot path that leaves the professional's
-- question committed with no answer beside it, after the model had already
-- produced one.
--
-- Expand-only: widening a varchar rewrites no rows and cannot fail on
-- existing data.
--
-- Same hardening pattern as the rest of this table's history.

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

alter table psychdeep_v12.therapist_copilot_messages
    alter column requested_model type varchar(160);

alter table psychdeep_v12.agent2_analysis_traces
    alter column requested_model type varchar(160),
    alter column response_model type varchar(160);

reset role;
revoke psychdeep_backend from postgres granted by postgres;

do $$
declare
    membership_is_restored boolean;
    narrow_columns integer;
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

    -- atttypmod is the declared length plus the 4-byte varlena header.
    select count(*) into narrow_columns
      from pg_attribute
     where attrelid in (
               'psychdeep_v12.therapist_copilot_messages'::regclass,
               'psychdeep_v12.agent2_analysis_traces'::regclass)
       and attname in ('requested_model', 'response_model')
       and attnum > 0
       and not attisdropped
       and atttypmod <> 164;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if narrow_columns <> 0 then
        raise exception 'A provenance model column is still narrower than the 160 the config accepts (% left)', narrow_columns;
    end if;
end
$$;

commit;
