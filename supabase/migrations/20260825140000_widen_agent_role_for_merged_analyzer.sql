-- PsychDeep 1.2: one analyser instead of two agents.
--
-- Agents 2 and 4 read the same patient text twice — one for linguistic
-- markers, one for social determinants — with disjoint schemas and nothing
-- to reconcile between them. They are now a single call producing both
-- blocks under a single trace, whose `agent_role` is `analyzer_merged`.
--
-- `ck_agent2_trace_agent_role` only accepts the two old values, so the very
-- first merged analysis would be rejected by the database. This widens it.
--
-- Expand-only, and deliberately so: the two retired values stay accepted.
-- Rows carrying them are already in the table and are the record of how the
-- analyses before this migration were produced. Dropping them from the
-- constraint would make that history unwritable-back and buy nothing.
--
-- Nothing is backfilled. An old trace was produced by one of the two
-- separate agents, and relabelling it `analyzer_merged` would assert that
-- it came from a call that had not been written yet.
--
-- Same hardening as the rest of this table's history: the work runs as
-- psychdeep_backend, with pre/post assertions that refuse to leave the
-- temporary role membership behind.

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

alter table psychdeep_v12.agent2_analysis_traces
    drop constraint if exists ck_agent2_trace_agent_role;

alter table psychdeep_v12.agent2_analysis_traces
    add constraint ck_agent2_trace_agent_role
    check (agent_role in ('analyzer_merged', 'agent2_linguistic', 'agent4_psychosocial'));

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a constraint that would
-- reject the first analysis the new code writes.
do $$
declare
    membership_is_restored boolean;
    accepts_merged boolean;
    accepts_legacy boolean;
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

    -- Assert on the constraint's own expression rather than on a trial
    -- insert: the table is FORCE RLS and backend-owned, so a probe row
    -- would need privileges this session has deliberately just given up.
    select pg_get_constraintdef(oid) like '%analyzer_merged%'
      into accepts_merged
      from pg_constraint
     where conname = 'ck_agent2_trace_agent_role'
       and conrelid = 'psychdeep_v12.agent2_analysis_traces'::regclass;

    select pg_get_constraintdef(oid) like '%agent2_linguistic%'
       and pg_get_constraintdef(oid) like '%agent4_psychosocial%'
      into accepts_legacy
      from pg_constraint
     where conname = 'ck_agent2_trace_agent_role'
       and conrelid = 'psychdeep_v12.agent2_analysis_traces'::regclass;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not coalesce(accepts_merged, false) then
        raise exception 'agent_role constraint does not accept analyzer_merged';
    end if;
    if not coalesce(accepts_legacy, false) then
        raise exception 'agent_role constraint stopped accepting the retired roles';
    end if;
end
$$;

commit;
