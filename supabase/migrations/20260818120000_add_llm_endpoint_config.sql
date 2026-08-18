-- PsychDeep 1.2: runtime LLM endpoint, and model provenance on every
-- interaction it produces.
--
-- Expand-only production migration. Three changes:
--
--   1. `llm_endpoint_configs` — which model serves the two inference agents.
--      Exactly one row is active; superseded rows are kept, never updated in
--      place, so "what was serving the app in March" stays answerable.
--   2. `chat_messages.provider / model / provider_base_url` — the model
--      behind one assistant turn. NULL on patient messages and on turns
--      built from the server-owned safety templates, which is the
--      distinction someone re-reading a crisis conversation needs.
--   3. `agent2_analysis_traces.provider_base_url` — where the analysis call
--      went. The model name alone stops identifying anything once the
--      endpoint is configurable: two deployments can both say
--      "llama-3.1-8b" and mean different weights on different machines.
--
-- Existing rows are left NULL rather than backfilled with today's provider.
-- Everything recorded before this migration was produced by the Anthropic
-- endpoint configured at the time, but writing that in now would be an
-- inference presented as a record; the reader is told "not recorded"
-- instead, and the application renders it as such.
--
-- `llm_endpoint_configs.api_key` may hold a credential for a hosted
-- endpoint, so the table gets the same hardening as the clinical tables:
-- owned by psychdeep_backend, FORCE RLS, a backend-only policy, and no
-- privileges for public, anon, authenticated or service_role.

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

-- 1. The active endpoint ---------------------------------------------------
create table if not exists psychdeep_v12.llm_endpoint_configs (
    id uuid primary key,
    provider varchar(32) not null default 'anthropic',
    label varchar(120) not null default '',
    base_url varchar(500),
    chat_model varchar(160) not null,
    analysis_model varchar(160) not null,
    api_key text,
    max_tokens integer not null default 4096,
    timeout_seconds integer not null default 120,
    is_active boolean not null default true,
    created_by uuid references psychdeep_v12.users(id) on delete set null,
    created_at timestamp not null default (now() at time zone 'utc'),
    deactivated_at timestamp,
    constraint ck_llm_endpoint_provider
        check (provider in ('anthropic', 'openai_compatible')),
    -- A local endpoint without a URL is unusable; a hosted one has none.
    constraint ck_llm_endpoint_base_url check (
        (provider = 'anthropic' and base_url is null)
        or
        (provider = 'openai_compatible' and base_url is not null)
    ),
    constraint ck_llm_endpoint_max_tokens check (max_tokens between 256 and 32768),
    constraint ck_llm_endpoint_timeout check (timeout_seconds between 5 and 600)
);

create index if not exists ix_llm_endpoint_active
    on psychdeep_v12.llm_endpoint_configs(is_active, created_at desc);

-- At most one active row, enforced by the database rather than by the
-- service that writes it: a second active endpoint would make "which model
-- answered" ambiguous for every row written afterwards.
create unique index if not exists ux_llm_endpoint_single_active
    on psychdeep_v12.llm_endpoint_configs(is_active)
    where is_active;

alter table psychdeep_v12.llm_endpoint_configs enable row level security;
alter table psychdeep_v12.llm_endpoint_configs force row level security;

drop policy if exists backend_full_access on psychdeep_v12.llm_endpoint_configs;
create policy backend_full_access
    on psychdeep_v12.llm_endpoint_configs
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.llm_endpoint_configs
    from public, anon, authenticated, service_role;

-- 2. Provenance on the assistant's turns -----------------------------------
alter table psychdeep_v12.chat_messages
    add column if not exists provider varchar(32),
    add column if not exists model varchar(160),
    add column if not exists provider_base_url varchar(500);

-- 3. Provenance on the analysis lineage ------------------------------------
alter table psychdeep_v12.agent2_analysis_traces
    add column if not exists provider_base_url varchar(500);

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a half-hardened table.
do $$
declare
    membership_is_restored boolean;
    table_is_hardened boolean;
    policy_exists boolean;
    single_active_enforced boolean;
    chat_columns integer;
    trace_column_exists boolean;
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
       and relation.relname = 'llm_endpoint_configs';

    select exists (
        select 1 from pg_policies
         where schemaname = 'psychdeep_v12'
           and tablename = 'llm_endpoint_configs'
           and policyname = 'backend_full_access'
           and 'psychdeep_backend' = any(roles)
    ) into policy_exists;

    select exists (
        select 1 from pg_indexes
         where schemaname = 'psychdeep_v12'
           and tablename = 'llm_endpoint_configs'
           and indexname = 'ux_llm_endpoint_single_active'
    ) into single_active_enforced;

    select count(*) into chat_columns
      from information_schema.columns
     where table_schema = 'psychdeep_v12'
       and table_name = 'chat_messages'
       and column_name in ('provider', 'model', 'provider_base_url');

    select exists (
        select 1 from information_schema.columns
         where table_schema = 'psychdeep_v12'
           and table_name = 'agent2_analysis_traces'
           and column_name = 'provider_base_url'
    ) into trace_column_exists;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'llm_endpoint_configs owner/RLS hardening is incomplete';
    end if;
    if not policy_exists then
        raise exception 'llm_endpoint_configs backend RLS policy is missing';
    end if;
    if not single_active_enforced then
        raise exception 'llm_endpoint_configs single-active index is missing';
    end if;
    if chat_columns <> 3 then
        raise exception 'chat_messages provenance columns are incomplete (found %)', chat_columns;
    end if;
    if not trace_column_exists then
        raise exception 'agent2_analysis_traces.provider_base_url was not added';
    end if;
end
$$;
