-- PsychDeep 1.2: psychosocial context (Agent 4).
--
-- Expand-only production migration. Two changes:
--
--   1. `agent2_analysis_traces.agent_role` — the lineage table now serves
--      every structured-extraction agent, not just Agent 2. Existing rows
--      are backfilled to 'agent2_linguistic', which is what they are.
--   2. `psychosocial_observations` — social determinants extracted from what
--      the patient wrote, each with the literal quote that supports it.
--
-- Both get the same hardening as the rest of the sensitive tables: owned by
-- psychdeep_backend, FORCE RLS, a backend-only policy, and no privileges for
-- public, anon, authenticated or service_role. `evidence_quote` holds a
-- bounded fragment of the patient's own text, so it is at least as sensitive
-- as chat_messages and is protected identically.

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

-- 1. Agent role on the shared lineage table -------------------------------
alter table psychdeep_v12.agent2_analysis_traces
    add column if not exists agent_role varchar(32) not null default 'agent2_linguistic';

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'ck_agent2_trace_agent_role'
          and conrelid = 'psychdeep_v12.agent2_analysis_traces'::regclass
    ) then
        alter table psychdeep_v12.agent2_analysis_traces
            add constraint ck_agent2_trace_agent_role
            check (agent_role in ('agent2_linguistic', 'agent4_psychosocial'));
    end if;
end
$$;

create index if not exists ix_agent2_trace_role_started
    on psychdeep_v12.agent2_analysis_traces(agent_role, started_at desc);

-- 2. Psychosocial observations --------------------------------------------
create table if not exists psychdeep_v12.psychosocial_observations (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id) on delete cascade,
    correlation_id uuid,
    trace_id uuid references psychdeep_v12.agent2_analysis_traces(id) on delete set null,
    source_type varchar(24) not null,
    chat_message_id uuid references psychdeep_v12.chat_messages(id) on delete cascade,
    diary_entry_id uuid references psychdeep_v12.diary_entries(id) on delete cascade,
    domain varchar(32) not null,
    category varchar(48) not null,
    valence varchar(16) not null,
    intensity double precision not null,
    confidence double precision not null,
    is_change boolean not null default false,
    summary text not null,
    evidence_quote text not null default '',
    status varchar(16) not null default 'inferred',
    adjudicated_by uuid,
    adjudicated_at timestamp,
    adjudication_note text,
    observed_at timestamp not null default (now() at time zone 'utc'),
    created_at timestamp not null default (now() at time zone 'utc'),
    constraint ck_psychosocial_source_type
        check (source_type in ('chat_message', 'diary_entry')),
    constraint ck_psychosocial_exact_source check (
        (source_type = 'chat_message' and chat_message_id is not null and diary_entry_id is null)
        or
        (source_type = 'diary_entry' and diary_entry_id is not null and chat_message_id is null)
    ),
    constraint ck_psychosocial_valence check (valence in ('risk', 'protective', 'neutral')),
    constraint ck_psychosocial_status check (status in ('inferred', 'confirmed', 'refuted')),
    constraint ck_psychosocial_intensity check (intensity >= 0 and intensity <= 1),
    constraint ck_psychosocial_confidence check (confidence >= 0 and confidence <= 1)
);

create index if not exists ix_psychosocial_observations_user_id
    on psychdeep_v12.psychosocial_observations(user_id);
create index if not exists ix_psychosocial_observations_domain
    on psychdeep_v12.psychosocial_observations(domain);
create index if not exists ix_psychosocial_observations_correlation_id
    on psychdeep_v12.psychosocial_observations(correlation_id);
create index if not exists ix_psychosocial_observations_trace_id
    on psychdeep_v12.psychosocial_observations(trace_id);
create index if not exists ix_psychosocial_observations_chat_message_id
    on psychdeep_v12.psychosocial_observations(chat_message_id);
create index if not exists ix_psychosocial_observations_diary_entry_id
    on psychdeep_v12.psychosocial_observations(diary_entry_id);
create index if not exists ix_psychosocial_observations_observed_at
    on psychdeep_v12.psychosocial_observations(observed_at);
create index if not exists ix_psychosocial_user_observed
    on psychdeep_v12.psychosocial_observations(user_id, observed_at desc);
create index if not exists ix_psychosocial_user_domain_observed
    on psychdeep_v12.psychosocial_observations(user_id, domain, observed_at desc);

alter table psychdeep_v12.psychosocial_observations enable row level security;
alter table psychdeep_v12.psychosocial_observations force row level security;

drop policy if exists backend_full_access on psychdeep_v12.psychosocial_observations;
create policy backend_full_access
    on psychdeep_v12.psychosocial_observations
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.psychosocial_observations
    from public, anon, authenticated, service_role;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a half-hardened table.
do $$
declare
    membership_is_restored boolean;
    table_is_hardened boolean;
    policy_exists boolean;
    role_column_exists boolean;
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

    select owner_role.rolname = 'psychdeep_backend'
       and relation.relrowsecurity
       and relation.relforcerowsecurity
      into table_is_hardened
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      join pg_roles owner_role on owner_role.oid = relation.relowner
     where namespace.nspname = 'psychdeep_v12'
       and relation.relname = 'psychosocial_observations';

    select exists (
        select 1 from pg_policies
         where schemaname = 'psychdeep_v12'
           and tablename = 'psychosocial_observations'
           and policyname = 'backend_full_access'
           and 'psychdeep_backend' = any(roles)
    ) into policy_exists;

    select exists (
        select 1 from information_schema.columns
         where table_schema = 'psychdeep_v12'
           and table_name = 'agent2_analysis_traces'
           and column_name = 'agent_role'
    ) into role_column_exists;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'psychosocial_observations owner/RLS hardening is incomplete';
    end if;
    if not policy_exists then
        raise exception 'psychosocial_observations backend RLS policy is missing';
    end if;
    if not role_column_exists then
        raise exception 'agent2_analysis_traces.agent_role was not added';
    end if;
end
$$;
