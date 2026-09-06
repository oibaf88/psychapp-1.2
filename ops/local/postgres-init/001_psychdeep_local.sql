-- Local/offline database bootstrap. This runs only when the Docker volume is
-- created for the first time.

create schema if not exists psychdeep_v12 authorization psychapp;

alter role psychapp in database psychapp
    set search_path = psychdeep_v12, public;

-- SymmetricDS runtime tables are deliberately kept outside the application
-- schema. The engine JDBC URL sets currentSchema=psychdeep_sync and every
-- PsychDeep table trigger names psychdeep_v12 explicitly.
create schema if not exists psychdeep_sync authorization psychapp;

-- Production creates this through an explicit Supabase migration. Local/dev
-- historically relied on SQLAlchemy create_all(), but llm_usage_events is a
-- metadata-only SQL ledger rather than an ORM model. Create its current shape
-- here so local inference has the same accounting guarantees as production.
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
    web_search_requests integer,
    web_fetch_requests integer,
    system_chars integer,
    message_chars integer,
    schema_chars integer,
    latency_ms integer,
    error_kind varchar(64),
    source_table varchar(64),
    source_id uuid,
    created_at timestamptz not null default now(),
    constraint ck_local_llm_usage_status check (status in ('succeeded','failed','historic_unknown')),
    constraint ck_local_llm_usage_nonnegative check (
        (max_tokens is null or max_tokens >= 0)
        and (input_tokens is null or input_tokens >= 0)
        and (output_tokens is null or output_tokens >= 0)
        and (thinking_tokens is null or thinking_tokens >= 0)
        and (cache_creation_input_tokens is null or cache_creation_input_tokens >= 0)
        and (cache_read_input_tokens is null or cache_read_input_tokens >= 0)
        and (cache_creation_5m_input_tokens is null or cache_creation_5m_input_tokens >= 0)
        and (cache_creation_1h_input_tokens is null or cache_creation_1h_input_tokens >= 0)
        and (web_search_requests is null or web_search_requests >= 0)
        and (web_fetch_requests is null or web_fetch_requests >= 0)
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
create unique index if not exists ux_llm_usage_provider_request
    on psychdeep_v12.llm_usage_events(provider, provider_request_id)
    where provider_request_id is not null;
