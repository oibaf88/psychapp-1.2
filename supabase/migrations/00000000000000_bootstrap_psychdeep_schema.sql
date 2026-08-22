-- PsychDeep 1.2: Supabase bootstrap — backend role, schema and base tables.
--
-- Run this BEFORE every other file in this directory. Production never runs
-- SQLAlchemy's create_all(), so on a Supabase project the base tables have to
-- be created explicitly; the other migrations in this directory are
-- expand-only and assume these tables already exist.
--
-- Idempotent by design: on the project that was bootstrapped by an earlier
-- create_all() run every CREATE is a no-op, and the hardening pass only
-- touches tables it can prove are owned by psychdeep_backend.
--
-- Deliberately NOT included here, because a later migration adds them:
--   * agent2_analysis_traces, alfa_signals.agent2_trace_id and the four
--     risk_assessments lineage columns   -> 20260815120000
--   * therapist_copilot_messages          -> 20260815160000
--   * agent2_analysis_traces.agent_role,
--     psychosocial_observations           -> 20260815180000
--   * llm_endpoint_configs, chat_messages provenance columns
--                                         -> 20260818120000
-- Applying bootstrap -> 20260815120000 -> ... -> 20260818120000 in filename
-- order therefore reproduces the exact schema the API expects.

begin;

-- 1. Backend role -----------------------------------------------------------
--
-- psychdeep_backend owns every application table. It is created without a
-- password: set one out of band, in the Supabase SQL editor, so it never
-- reaches a tracked file.
--
--     alter role psychdeep_backend with password '<generated>';
--
-- NOINHERIT keeps the role's privileges out of any session that merely holds
-- membership in it — they have to be taken deliberately with SET ROLE.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'psychdeep_backend') then
        create role psychdeep_backend login noinherit;
        raise notice 'Created role psychdeep_backend. Set its password before deploying.';
    end if;
end
$$;

-- 2. Schema -----------------------------------------------------------------
--
-- A dedicated schema rather than `public`: PostgREST exposes `public`, so a
-- table created there is reachable with the publishable anon key unless it is
-- locked down afterwards. `psychdeep_v12` is not exposed at all.
create schema if not exists psychdeep_v12;

-- The schema is left owned by whoever created it — on the original project
-- that is postgres, and taking ownership away from it here would be a change
-- to live infrastructure this file has no reason to make. What the backend
-- needs is USAGE and CREATE, which is granted either way.
grant usage, create on schema psychdeep_v12 to psychdeep_backend;
revoke all on schema psychdeep_v12 from public, anon, authenticated, service_role;

-- 3. Base tables ------------------------------------------------------------
grant psychdeep_backend to postgres with set true;
set local role psychdeep_backend;

create table if not exists psychdeep_v12.users (
    id uuid primary key,
    email varchar(255) not null,
    hashed_password varchar(255) not null,
    display_name varchar(255) not null,
    role varchar(32) not null,
    locale varchar(10) not null,
    phone_verified boolean not null,
    is_active boolean not null,
    created_at timestamp without time zone not null,
    constraint ck_users_role
        check (role in ('patient', 'therapist', 'supervisor', 'admin_clinical'))
);
create unique index if not exists ix_users_email on psychdeep_v12.users (email);

create table if not exists psychdeep_v12.password_reset_tokens (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    token varchar(128) not null,
    expires_at timestamp without time zone not null,
    is_used boolean not null,
    created_at timestamp without time zone not null
);
create unique index if not exists ix_password_reset_tokens_token
    on psychdeep_v12.password_reset_tokens (token);

create table if not exists psychdeep_v12.user_consents (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    consent_type varchar(64) not null,
    version varchar(32) not null,
    granted boolean not null,
    granted_at timestamp without time zone not null,
    revoked_at timestamp without time zone
);

create table if not exists psychdeep_v12.patient_professional_assignments (
    id uuid primary key,
    patient_id uuid not null references psychdeep_v12.users(id),
    professional_id uuid not null references psychdeep_v12.users(id),
    status varchar(32) not null,
    requested_at timestamp without time zone not null,
    updated_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.confirmed_facts (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    category varchar(64) not null,
    content text not null,
    declared_by varchar(16) not null,
    is_active boolean not null,
    superseded_by uuid,
    created_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.alfa_signals (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    signal_type varchar(64) not null,
    value json not null,
    confidence_band varchar(16),
    is_active boolean not null,
    superseded_by_fact uuid,
    timestamp timestamp without time zone not null
);

create table if not exists psychdeep_v12.baselines (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    window_start timestamp without time zone not null,
    window_end timestamp without time zone not null,
    stats json not null,
    is_active boolean not null,
    created_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.biometric_data (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    device_type varchar(64) not null,
    heart_rate_avg double precision,
    heart_rate_variability double precision,
    sleep_duration_hours double precision,
    sleep_quality_score double precision,
    deep_sleep_hours double precision,
    rem_sleep_hours double precision,
    steps integer,
    active_calories double precision,
    measured_at timestamp without time zone not null,
    created_at timestamp without time zone not null
);
create index if not exists ix_biometric_data_user_id
    on psychdeep_v12.biometric_data (user_id);

create table if not exists psychdeep_v12.app_usage_data (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    apps_usage_stats json not null,
    screen_time_minutes integer,
    measured_at timestamp without time zone not null,
    created_at timestamp without time zone not null
);
create index if not exists ix_app_usage_data_user_id
    on psychdeep_v12.app_usage_data (user_id);

create table if not exists psychdeep_v12.check_ins (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    mood integer not null,
    craving integer not null,
    sleep_hours double precision not null,
    self_efficacy integer not null,
    notes text,
    created_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.diary_entries (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    content text not null,
    created_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.risk_assessments (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    alert_level integer not null,
    triggering_rules json not null,
    input_signals json not null,
    input_facts json,
    confidence double precision,
    assessment_reason text not null,
    model_version varchar(32) not null,
    calculated_at timestamp without time zone not null,
    generated_alert_id uuid,
    constraint ck_risk_alert_level check (alert_level between 0 and 4)
);

create table if not exists psychdeep_v12.professional_alerts (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    alert_level integer not null,
    status varchar(16) not null,
    source varchar(32) not null,
    title varchar(255) not null,
    description text not null,
    related_signals json,
    related_assessment_id uuid,
    created_at timestamp without time zone not null,
    acknowledged_at timestamp without time zone,
    resolved_at timestamp without time zone,
    resolution_notes text,
    dismiss_reason text
);

create table if not exists psychdeep_v12.notifications (
    id uuid primary key,
    user_id uuid references psychdeep_v12.users(id),
    professional_id uuid references psychdeep_v12.users(id),
    recipient_type varchar(16) not null,
    channel varchar(16) not null,
    alert_level integer,
    template_code varchar(64) not null,
    title varchar(255),
    body text not null,
    status varchar(16) not null,
    related_alert_id uuid,
    related_assessment_id uuid,
    created_at timestamp without time zone not null,
    sent_at timestamp without time zone,
    read_at timestamp without time zone
);

create table if not exists psychdeep_v12.safety_plans (
    id uuid primary key,
    user_id uuid not null unique references psychdeep_v12.users(id),
    warning_signs text,
    coping_strategies text,
    social_supports text,
    professional_contacts text,
    safe_environment text,
    reasons_to_live text,
    updated_at timestamp without time zone not null
);

-- Holds the patient's own words, so it is as sensitive as the diary.
create table if not exists psychdeep_v12.chat_messages (
    id uuid primary key,
    user_id uuid not null references psychdeep_v12.users(id),
    role varchar(16) not null,
    content text not null,
    ui_mode varchar(16),
    created_at timestamp without time zone not null
);

create table if not exists psychdeep_v12.audit_log (
    id uuid primary key,
    actor_id uuid,
    actor_role varchar(32),
    action varchar(128) not null,
    entity_type varchar(64),
    entity_id varchar(64),
    extra json,
    created_at timestamp without time zone not null
);

-- 4. Hardening --------------------------------------------------------------
--
-- Same contract the later migrations apply to the tables they add: FORCE RLS
-- with a single policy for the backend role, and no privileges at all for the
-- PostgREST roles. FORCE is what makes the policy meaningful — without it the
-- owner bypasses RLS.
--
-- A table this transaction did not create may be owned by another role (an
-- older create_all() run under a different DATABASE_URL user). Forcing RLS on
-- one of those would lock its own writer out, so ownership is checked first
-- and a foreign-owned table is reported instead of altered.
do $$
declare
    app_table record;
    forced int := 0;
    skipped int := 0;
begin
    for app_table in
        select relation.relname,
               owner_role.rolname = 'psychdeep_backend' as owned_by_backend
          from pg_class relation
          join pg_namespace namespace on namespace.oid = relation.relnamespace
          join pg_roles owner_role on owner_role.oid = relation.relowner
         where namespace.nspname = 'psychdeep_v12'
           and relation.relkind = 'r'
         order by relation.relname
    loop
        if app_table.owned_by_backend then
            execute format(
                'alter table psychdeep_v12.%I enable row level security',
                app_table.relname);
            execute format(
                'alter table psychdeep_v12.%I force row level security',
                app_table.relname);
            execute format(
                'drop policy if exists backend_full_access on psychdeep_v12.%I',
                app_table.relname);
            execute format(
                'create policy backend_full_access on psychdeep_v12.%I '
                'for all to psychdeep_backend using (true) with check (true)',
                app_table.relname);
            forced := forced + 1;
        else
            skipped := skipped + 1;
            raise warning
                'psychdeep_v12.% is not owned by psychdeep_backend; RLS left unchanged',
                app_table.relname;
        end if;

        execute format(
            'revoke all on table psychdeep_v12.%I from public, anon, authenticated, service_role',
            app_table.relname);
    end loop;

    raise notice 'Hardened % table(s); % left to their owner.', forced, skipped;
end
$$;

reset role;

-- 5. Give the temporary SET privilege back ----------------------------------
--
-- Only the row this file added: PostgreSQL records the bootstrap superuser as
-- the grantor of the membership a CREATEROLE user gets over a role it creates,
-- so `grant ... with set true` above added a second row rather than mutating
-- that one. Revoking by grantor removes exactly the temporary one and leaves
-- the shape the expand migrations check before they will run.
revoke psychdeep_backend from postgres granted by postgres;

-- 6. Verification -----------------------------------------------------------
--
-- Fail the whole transaction rather than commit a half-built schema.
do $$
declare
    missing_tables text[];
    membership_is_expected boolean;
begin
    select array_agg(expected.table_name order by expected.table_name)
      into missing_tables
      from (values
                ('users'), ('password_reset_tokens'), ('user_consents'),
                ('patient_professional_assignments'), ('confirmed_facts'),
                ('alfa_signals'), ('baselines'), ('biometric_data'),
                ('app_usage_data'), ('check_ins'), ('diary_entries'),
                ('risk_assessments'), ('professional_alerts'), ('notifications'),
                ('safety_plans'), ('chat_messages'), ('audit_log')
           ) as expected(table_name)
     where to_regclass('psychdeep_v12.' || quote_ident(expected.table_name)) is null;

    if missing_tables is not null then
        raise exception 'Bootstrap did not create: %', array_to_string(missing_tables, ', ');
    end if;

    -- The shape the expand migrations check before they will run.
    select count(*) = 1
       and bool_and(m.admin_option)
       and not bool_or(m.inherit_option)
       and not bool_or(m.set_option)
      into membership_is_expected
      from pg_auth_members m
      join pg_roles granted_role on granted_role.oid = m.roleid
      join pg_roles member_role on member_role.oid = m.member
     where granted_role.rolname = 'psychdeep_backend'
       and member_role.rolname = 'postgres';

    if not coalesce(membership_is_expected, false) then
        raise exception 'postgres -> psychdeep_backend membership was not left in the expected shape';
    end if;
end
$$;

commit;
