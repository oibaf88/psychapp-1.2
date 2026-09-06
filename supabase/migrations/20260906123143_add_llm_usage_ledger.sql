-- PsychDeep: provider-usage ledger for cost reconciliation.
-- Clinical text is NOT copied here. The ledger stores only provider metadata,
-- token counters and request-size measurements.

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

create table if not exists psychdeep_v12.llm_usage_events (
    id uuid primary key,
    call_kind varchar(32) not null,
    agent_role varchar(48) not null,
    status varchar(32) not null,
    provider varchar(32) not null,
    provider_base_url varchar(500),
    requested_model varchar(160) not null,
    response_model varchar(160),
    effort varchar(16),
    max_tokens integer,
    provider_message_id varchar(128),
    provider_request_id varchar(128),
    stop_reason varchar(64),
    input_tokens integer,
    output_tokens integer,
    thinking_tokens integer,
    cache_creation_input_tokens integer,
    cache_read_input_tokens integer,
    cache_creation_5m_input_tokens integer,
    cache_creation_1h_input_tokens integer,
    system_chars integer,
    message_chars integer,
    schema_chars integer,
    latency_ms integer,
    error_kind varchar(64),
    source_table varchar(64),
    source_id uuid,
    created_at timestamptz not null default now(),
    constraint ck_llm_usage_status check (status in ('succeeded','failed','historic_unknown')),
    constraint ck_llm_usage_nonnegative check (
        (max_tokens is null or max_tokens >= 0)
        and (input_tokens is null or input_tokens >= 0)
        and (output_tokens is null or output_tokens >= 0)
        and (thinking_tokens is null or thinking_tokens >= 0)
        and (cache_creation_input_tokens is null or cache_creation_input_tokens >= 0)
        and (cache_read_input_tokens is null or cache_read_input_tokens >= 0)
        and (cache_creation_5m_input_tokens is null or cache_creation_5m_input_tokens >= 0)
        and (cache_creation_1h_input_tokens is null or cache_creation_1h_input_tokens >= 0)
        and (system_chars is null or system_chars >= 0)
        and (message_chars is null or message_chars >= 0)
        and (schema_chars is null or schema_chars >= 0)
        and (latency_ms is null or latency_ms >= 0)
    )
);

create index if not exists ix_llm_usage_created
    on psychdeep_v12.llm_usage_events(created_at desc);
create index if not exists ix_llm_usage_agent_created
    on psychdeep_v12.llm_usage_events(agent_role, created_at desc);
create index if not exists ix_llm_usage_model_created
    on psychdeep_v12.llm_usage_events(requested_model, created_at desc);
create unique index if not exists ux_llm_usage_provider_request
    on psychdeep_v12.llm_usage_events(provider, provider_request_id)
    where provider_request_id is not null;
create unique index if not exists ux_llm_usage_historic_source
    on psychdeep_v12.llm_usage_events(source_table, source_id)
    where source_table is not null and source_id is not null;

alter table psychdeep_v12.llm_usage_events enable row level security;
alter table psychdeep_v12.llm_usage_events force row level security;

drop policy if exists backend_full_access on psychdeep_v12.llm_usage_events;
create policy backend_full_access
    on psychdeep_v12.llm_usage_events
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.llm_usage_events from public, anon, authenticated, service_role;

-- Exact historic structured-analysis usage. Source text is not copied; only
-- its character count is carried so cost can be related to actual payload.
insert into psychdeep_v12.llm_usage_events (
    id, call_kind, agent_role, status, provider, provider_base_url,
    requested_model, response_model, effort, max_tokens,
    provider_message_id, provider_request_id, stop_reason,
    input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
    message_chars, latency_ms, error_kind, source_table, source_id, created_at
)
select
    gen_random_uuid(),
    'structured_analysis',
    coalesce(t.agent_role, 'analyzer_merged'),
    case when t.status = 'succeeded' then 'succeeded' else 'failed' end,
    t.provider,
    t.provider_base_url,
    t.requested_model,
    t.response_model,
    t.effort,
    t.max_tokens,
    t.provider_message_id,
    t.provider_request_id,
    t.stop_reason,
    t.input_tokens,
    t.output_tokens,
    t.cache_creation_input_tokens,
    t.cache_read_input_tokens,
    case
        when t.source_type = 'chat_message' then length(cm.content)
        when t.source_type = 'diary_entry' then length(de.content)
        else null
    end,
    t.latency_ms,
    t.error_kind,
    'agent2_analysis_traces',
    t.id,
    t.started_at
from psychdeep_v12.agent2_analysis_traces t
left join psychdeep_v12.chat_messages cm on cm.id = t.chat_message_id
left join psychdeep_v12.diary_entries de on de.id = t.diary_entry_id
on conflict do nothing;

-- Historic conversational calls are known to have happened, but the old
-- schema discarded their token usage. Keep them explicitly NULL instead of
-- fabricating a count.
insert into psychdeep_v12.llm_usage_events (
    id, call_kind, agent_role, status, provider, provider_base_url,
    requested_model, response_model, source_table, source_id, created_at
)
select
    gen_random_uuid(), 'chat', 'agent1_chat', 'historic_unknown',
    cm.provider, cm.provider_base_url, cm.model, cm.model,
    'chat_messages', cm.id, cm.created_at at time zone 'UTC'
from psychdeep_v12.chat_messages cm
where cm.role = 'assistant'
  and cm.provider is not null
  and cm.model is not null
on conflict do nothing;

insert into psychdeep_v12.llm_usage_events (
    id, call_kind, agent_role, status, provider,
    requested_model, response_model, error_kind, source_table, source_id, created_at
)
select
    gen_random_uuid(), 'chat', 'agent3_copilot', 'historic_unknown',
    c.provider, c.requested_model,
    nullif(c.context_counts->>'response_model', ''), c.error_kind,
    'therapist_copilot_messages', c.id, c.created_at at time zone 'UTC'
from psychdeep_v12.therapist_copilot_messages c
where c.role = 'assistant'
  and c.provider is not null
  and c.requested_model is not null
on conflict do nothing;

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
       and relation.relname = 'llm_usage_events'
       and relation.relkind = 'r';

    if not coalesce(membership_is_restored, false) then
        raise exception 'temporary postgres -> psychdeep_backend membership was not removed';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'llm_usage_events owner/RLS hardening verification failed';
    end if;
end
$$;

commit;
