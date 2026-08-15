-- PsychDeep 1.2: deterministic risk explanations + Agent 2 lineage.
--
-- Expand-only production migration. Existing rows remain valid and are
-- intentionally not backfilled with invented lineage.

begin;

-- Supabase currently grants postgres membership in psychdeep_backend with
-- ADMIN=TRUE, INHERIT=FALSE and SET=FALSE. Refuse to proceed if that
-- precondition has drifted. Granting only SET creates a separate row whose
-- grantor is postgres; specifying all membership options here would instead
-- mutate Supabase's existing row. The separate row is revoked before commit.
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

create table if not exists psychdeep_v12.agent2_analysis_traces (
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
    error_kind varchar(64),
    error_code varchar(64),
    http_status integer,
    app_release varchar(64) not null default 'local',
    started_at timestamptz not null,
    completed_at timestamptz,
    created_at timestamptz not null,
    constraint ck_agent2_trace_source_type
        check (source_type in ('chat_message', 'diary_entry')),
    constraint ck_agent2_trace_exact_source check (
        (source_type = 'chat_message' and chat_message_id is not null and diary_entry_id is null)
        or
        (source_type = 'diary_entry' and diary_entry_id is not null and chat_message_id is null)
    ),
    constraint ck_agent2_trace_status check (
        status in (
            'started', 'succeeded', 'refused', 'invalid_output',
            'configuration_error', 'provider_error', 'timeout', 'abandoned'
        )
    ),
    constraint ck_agent2_trace_input_tokens check (input_tokens is null or input_tokens >= 0),
    constraint ck_agent2_trace_output_tokens check (output_tokens is null or output_tokens >= 0),
    constraint ck_agent2_trace_latency check (latency_ms is null or latency_ms >= 0),
    constraint ck_agent2_trace_completion check (completed_at is null or completed_at >= started_at)
);

alter table psychdeep_v12.alfa_signals
    add column if not exists agent2_trace_id uuid;

alter table psychdeep_v12.risk_assessments
    add column if not exists correlation_id uuid,
    add column if not exists agent2_trace_id uuid,
    add column if not exists linguistic_signal_id_used uuid,
    add column if not exists calculation_trace jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'fk_alfa_signals_agent2_trace'
          and conrelid = 'psychdeep_v12.alfa_signals'::regclass
    ) then
        alter table psychdeep_v12.alfa_signals
            add constraint fk_alfa_signals_agent2_trace
            foreign key (agent2_trace_id)
            references psychdeep_v12.agent2_analysis_traces(id)
            on delete set null;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'fk_risk_assessments_agent2_trace'
          and conrelid = 'psychdeep_v12.risk_assessments'::regclass
    ) then
        alter table psychdeep_v12.risk_assessments
            add constraint fk_risk_assessments_agent2_trace
            foreign key (agent2_trace_id)
            references psychdeep_v12.agent2_analysis_traces(id)
            on delete set null;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'fk_risk_assessments_linguistic_signal'
          and conrelid = 'psychdeep_v12.risk_assessments'::regclass
    ) then
        alter table psychdeep_v12.risk_assessments
            add constraint fk_risk_assessments_linguistic_signal
            foreign key (linguistic_signal_id_used)
            references psychdeep_v12.alfa_signals(id)
            on delete set null;
    end if;
end
$$;

create index if not exists ix_agent2_analysis_traces_correlation_id
    on psychdeep_v12.agent2_analysis_traces(correlation_id);
create index if not exists ix_agent2_analysis_traces_user_id
    on psychdeep_v12.agent2_analysis_traces(user_id);
create index if not exists ix_agent2_trace_user_started
    on psychdeep_v12.agent2_analysis_traces(user_id, started_at desc);
create index if not exists ix_agent2_trace_status_started
    on psychdeep_v12.agent2_analysis_traces(status, started_at desc);
create index if not exists ix_agent2_analysis_traces_chat_message_id
    on psychdeep_v12.agent2_analysis_traces(chat_message_id);
create index if not exists ix_agent2_analysis_traces_diary_entry_id
    on psychdeep_v12.agent2_analysis_traces(diary_entry_id);
create index if not exists ix_alfa_signals_agent2_trace_id
    on psychdeep_v12.alfa_signals(agent2_trace_id);
create index if not exists ix_risk_assessments_correlation_id
    on psychdeep_v12.risk_assessments(correlation_id);
create index if not exists ix_risk_assessments_agent2_trace_id
    on psychdeep_v12.risk_assessments(agent2_trace_id);
create index if not exists ix_risk_assessments_linguistic_signal_id_used
    on psychdeep_v12.risk_assessments(linguistic_signal_id_used);

alter table psychdeep_v12.agent2_analysis_traces enable row level security;
alter table psychdeep_v12.agent2_analysis_traces force row level security;

drop policy if exists backend_full_access on psychdeep_v12.agent2_analysis_traces;
create policy backend_full_access
    on psychdeep_v12.agent2_analysis_traces
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.agent2_analysis_traces from public, anon, authenticated, service_role;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

do $$
declare
    membership_is_restored boolean;
    table_is_hardened boolean;
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
       and relation.relname = 'agent2_analysis_traces'
       and relation.relkind = 'r';

    if not coalesce(membership_is_restored, false) then
        raise exception 'temporary postgres -> psychdeep_backend membership was not removed';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'agent2_analysis_traces owner/RLS hardening verification failed';
    end if;
end
$$;

commit;
