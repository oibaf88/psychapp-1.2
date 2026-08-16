-- PsychDeep 1.2: structured psychosocial context (Agent 4).
--
-- Adds the two tables behind the psychosocial layer:
--
--   psychosocial_extraction_traces -- lineage for each Agent 4 call, same
--       contract as agent2_analysis_traces: committed before the outbound
--       request, never duplicating clinical content.
--   psychosocial_observations      -- one structured reading per domain
--       (housing, money, household, support, losses, perceived
--       burdensomeness, leave-taking signals...) with the literal quote it
--       came from, superseded per domain rather than expiring.
--
-- Expand-only: nothing existing is altered, so a running release keeps
-- working until the new application code is deployed. Both tables hold
-- clinical data about an identified patient and therefore get the same
-- ownership and FORCE RLS treatment as agent2_analysis_traces and
-- therapist_copilot_messages: owned by psychdeep_backend, reachable only
-- through the backend role, no privileges for public, anon, authenticated
-- or service_role.

begin;

-- Supabase grants postgres membership in psychdeep_backend with ADMIN=TRUE,
-- INHERIT=FALSE and SET=FALSE. Refuse to proceed if that precondition has
-- drifted, exactly as the previous migrations do. The extra SET grant below
-- creates a separate membership row (grantor postgres) which is revoked
-- before commit, leaving Supabase's own row untouched.
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

-- --------------------------------------------------------------- traces ---
create table if not exists psychdeep_v12.psychosocial_extraction_traces (
    id uuid primary key,
    correlation_id uuid not null,
    user_id uuid not null references psychdeep_v12.users(id) on delete cascade,
    source_type varchar(24) not null,
    chat_message_id uuid references psychdeep_v12.chat_messages(id) on delete cascade,
    diary_entry_id uuid references psychdeep_v12.diary_entries(id) on delete cascade,
    status varchar(32) not null default 'started',
    provider varchar(32) not null default 'anthropic',
    requested_model varchar(128) not null,
    response_model varchar(128),
    effort varchar(16) not null,
    max_tokens integer not null,
    prompt_version varchar(64) not null,
    prompt_sha256 varchar(64) not null,
    schema_version varchar(64) not null,
    schema_sha256 varchar(64) not null,
    provider_message_id varchar(128),
    provider_request_id varchar(128),
    stop_reason varchar(64),
    input_tokens integer,
    output_tokens integer,
    cache_creation_input_tokens integer,
    cache_read_input_tokens integer,
    latency_ms integer,
    observation_count integer,
    error_kind varchar(64),
    error_code varchar(64),
    http_status integer,
    app_release varchar(64) not null default 'local',
    started_at timestamptz not null,
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    constraint ck_psychosocial_trace_source_type
        check (source_type in ('chat_message', 'diary_entry')),
    constraint ck_psychosocial_trace_exact_source check (
        (source_type = 'chat_message' and chat_message_id is not null and diary_entry_id is null) or
        (source_type = 'diary_entry' and diary_entry_id is not null and chat_message_id is null)
    ),
    constraint ck_psychosocial_trace_status check (
        status in ('started', 'succeeded', 'refused', 'invalid_output', 'configuration_error',
                   'provider_error', 'timeout', 'abandoned')
    ),
    constraint ck_psychosocial_trace_input_tokens check (input_tokens is null or input_tokens >= 0),
    constraint ck_psychosocial_trace_output_tokens check (output_tokens is null or output_tokens >= 0),
    constraint ck_psychosocial_trace_latency check (latency_ms is null or latency_ms >= 0),
    constraint ck_psychosocial_trace_observation_count
        check (observation_count is null or observation_count >= 0)
);

create index if not exists ix_psychosocial_trace_correlation_id
    on psychdeep_v12.psychosocial_extraction_traces(correlation_id);
create index if not exists ix_psychosocial_trace_user_started
    on psychdeep_v12.psychosocial_extraction_traces(user_id, started_at);
create index if not exists ix_psychosocial_trace_status_started
    on psychdeep_v12.psychosocial_extraction_traces(status, started_at);
create index if not exists ix_psychosocial_trace_chat_message_id
    on psychdeep_v12.psychosocial_extraction_traces(chat_message_id);
create index if not exists ix_psychosocial_trace_diary_entry_id
    on psychdeep_v12.psychosocial_extraction_traces(diary_entry_id);

-- --------------------------------------------------------- observations ---
create table if not exists psychdeep_v12.psychosocial_observations (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id) on delete cascade,
    domain varchar(48) not null,
    state varchar(24) not null,
    direction varchar(16) not null default 'desconocido',
    onset varchar(16) not null default 'desconocido',
    confidence double precision not null default 0,
    summary text not null,
    evidence_quote text,
    source_type varchar(24) not null,
    source_id uuid,
    extraction_trace_id uuid
        references psychdeep_v12.psychosocial_extraction_traces(id) on delete set null,
    correlation_id uuid,
    recorded_by varchar(16) not null default 'agent4',
    is_current boolean not null default true,
    superseded_by uuid,
    confirmed_fact_id uuid,
    dismissed_at timestamp,
    dismissed_reason text,
    observed_at timestamp not null default (now() at time zone 'utc'),
    created_at timestamp not null default (now() at time zone 'utc'),
    constraint ck_psychosocial_state
        check (state in ('protector', 'neutro', 'riesgo_leve', 'riesgo_moderado', 'riesgo_alto')),
    constraint ck_psychosocial_direction
        check (direction in ('mejora', 'estable', 'empeora', 'desconocido')),
    constraint ck_psychosocial_onset
        check (onset in ('reciente', 'cronico', 'desconocido')),
    constraint ck_psychosocial_confidence check (confidence >= 0 and confidence <= 1),
    constraint ck_psychosocial_source_type
        check (source_type in ('chat_message', 'diary_entry', 'professional')),
    constraint ck_psychosocial_recorded_by
        check (recorded_by in ('agent4', 'professional', 'user'))
);

create index if not exists ix_psychosocial_observations_user_id
    on psychdeep_v12.psychosocial_observations(user_id);
create index if not exists ix_psychosocial_observations_domain
    on psychdeep_v12.psychosocial_observations(domain);
create index if not exists ix_psychosocial_observations_source_id
    on psychdeep_v12.psychosocial_observations(source_id);
create index if not exists ix_psychosocial_observations_correlation_id
    on psychdeep_v12.psychosocial_observations(correlation_id);
create index if not exists ix_psychosocial_observations_trace_id
    on psychdeep_v12.psychosocial_observations(extraction_trace_id);
create index if not exists ix_psychosocial_observations_is_current
    on psychdeep_v12.psychosocial_observations(is_current);
-- The two access patterns that matter: "current picture of this patient"
-- (risk engine, on every evaluation) and "history of this patient" (panel).
create index if not exists ix_psychosocial_user_domain_current
    on psychdeep_v12.psychosocial_observations(user_id, domain, is_current);
create index if not exists ix_psychosocial_user_observed
    on psychdeep_v12.psychosocial_observations(user_id, observed_at);

-- ------------------------------------------------------------ hardening ---
alter table psychdeep_v12.psychosocial_extraction_traces enable row level security;
alter table psychdeep_v12.psychosocial_extraction_traces force row level security;
alter table psychdeep_v12.psychosocial_observations enable row level security;
alter table psychdeep_v12.psychosocial_observations force row level security;

drop policy if exists backend_full_access on psychdeep_v12.psychosocial_extraction_traces;
create policy backend_full_access
    on psychdeep_v12.psychosocial_extraction_traces
    for all
    to psychdeep_backend
    using (true)
    with check (true);

drop policy if exists backend_full_access on psychdeep_v12.psychosocial_observations;
create policy backend_full_access
    on psychdeep_v12.psychosocial_observations
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.psychosocial_extraction_traces
    from public, anon, authenticated, service_role;
revoke all on table psychdeep_v12.psychosocial_observations
    from public, anon, authenticated, service_role;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit half-hardened tables.
do $$
declare
    membership_is_restored boolean;
    tables_are_hardened boolean;
    policies_exist boolean;
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

    select count(*) = 2
       and bool_and(owner_role.rolname = 'psychdeep_backend')
       and bool_and(relation.relrowsecurity)
       and bool_and(relation.relforcerowsecurity)
      into tables_are_hardened
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      join pg_roles owner_role on owner_role.oid = relation.relowner
     where namespace.nspname = 'psychdeep_v12'
       and relation.relname in ('psychosocial_extraction_traces', 'psychosocial_observations');

    select count(*) = 2
      into policies_exist
      from pg_policies
     where schemaname = 'psychdeep_v12'
       and tablename in ('psychosocial_extraction_traces', 'psychosocial_observations')
       and policyname = 'backend_full_access'
       and 'psychdeep_backend' = any(roles);

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not removed';
    end if;
    if not coalesce(tables_are_hardened, false) then
        raise exception 'psychosocial tables owner/RLS hardening is incomplete';
    end if;
    if not coalesce(policies_exist, false) then
        raise exception 'psychosocial backend RLS policies are missing';
    end if;
end
$$;

commit;
