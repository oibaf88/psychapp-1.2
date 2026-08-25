-- PsychDeep — Supabase readiness check. Run this BEFORE the Render deploy.
--
-- It reproduces, as one query, the contract the API enforces at startup
-- (`_verify_production_schema` in backend/app/main.py) plus the hardening the
-- migrations are supposed to have left behind. Every row must read `ok`.
--
-- A failing row here is a Render deploy that would either refuse to start or
-- start against a half-migrated database, so it is cheaper to see it now.
--
-- Read-only: it changes nothing. Safe to run against a live project.
--
-- >>> Set the schema on the next line if yours is not psychdeep_v12. <<<

with settings as (
    select 'psychdeep_v12'::name as target_schema   -- <<< EDIT THIS
),
app_tables as (
    select relation.oid,
           relation.relname,
           pg_get_userbyid(relation.relowner) as owner,
           relation.relrowsecurity,
           relation.relforcerowsecurity
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      join settings on namespace.nspname = settings.target_schema
     where relation.relkind = 'r'
),
expected_tables(table_name) as (values
    ('agent2_analysis_traces'), ('alfa_signals'), ('app_usage_data'),
    ('audit_log'), ('baselines'), ('biometric_data'), ('chat_messages'),
    ('check_ins'), ('confirmed_facts'), ('diary_entries'),
    ('llm_endpoint_configs'), ('notifications'), ('password_reset_tokens'),
    ('patient_professional_assignments'), ('professional_alerts'),
    ('psychosocial_observations'), ('risk_assessments'), ('safety_plans'),
    ('therapist_copilot_messages'), ('user_consents'), ('users')
),
-- Exactly the set backend/app/main.py refuses to start without, plus the
-- provenance columns the runtime-endpoint release writes on every turn.
required_columns(table_name, column_name) as (values
    ('agent2_analysis_traces', 'id'),
    ('agent2_analysis_traces', 'agent_role'),
    ('agent2_analysis_traces', 'provider_base_url'),
    ('alfa_signals', 'agent2_trace_id'),
    ('risk_assessments', 'correlation_id'),
    ('risk_assessments', 'agent2_trace_id'),
    ('risk_assessments', 'linguistic_signal_id_used'),
    ('risk_assessments', 'calculation_trace'),
    ('therapist_copilot_messages', 'id'),
    ('psychosocial_observations', 'id'),
    ('psychosocial_observations', 'evidence_quote'),
    ('llm_endpoint_configs', 'id'),
    ('llm_endpoint_configs', 'copilot_model'),
    ('chat_messages', 'provider'),
    ('chat_messages', 'model'),
    ('chat_messages', 'provider_base_url')
),
checks(sort_key, check_name, failures) as (
    select 1, 'schema exists',
           (select count(*) from settings
             where not exists (select 1 from pg_namespace
                                where nspname = settings.target_schema))

    union all
    select 2, 'psychdeep_backend role exists',
           (select count(*) from (select 1) as one
             where not exists (select 1 from pg_roles where rolname = 'psychdeep_backend'))

    union all
    select 3, 'all application tables present',
           (select count(*) from expected_tables
             where table_name not in (select relname from app_tables))

    union all
    select 4, 'every table owned by psychdeep_backend',
           (select count(*) from app_tables where owner <> 'psychdeep_backend')

    union all
    select 5, 'every table has RLS enabled and forced',
           (select count(*) from app_tables
             where not relrowsecurity or not relforcerowsecurity)

    union all
    -- FORCE RLS makes the owner subject to policies too, so the backend needs
    -- one of its own or it locks itself out.
    select 6, 'every table has the backend_full_access policy',
           (select count(*) from app_tables
             where not exists (
                 select 1 from pg_policies, settings
                  where pg_policies.schemaname = settings.target_schema
                    and pg_policies.tablename = app_tables.relname
                    and pg_policies.policyname = 'backend_full_access'
                    and 'psychdeep_backend' = any(pg_policies.roles)))

    union all
    -- Anything readable by anon or authenticated is readable with the
    -- publishable key over PostgREST.
    select 7, 'no PostgREST role can read any table',
           (select count(*) from app_tables
             where has_table_privilege('anon', oid, 'SELECT')
                or has_table_privilege('authenticated', oid, 'SELECT')
                or has_table_privilege('service_role', oid, 'SELECT'))

    union all
    -- pg_attribute, not information_schema: the latter hides columns the
    -- caller has no privileges on, which is every one of these for postgres.
    select 8, 'API startup schema contract satisfied',
           (select count(*) from required_columns, settings
             where not exists (
                 select 1 from pg_attribute
                  where attrelid = to_regclass(settings.target_schema || '.'
                                               || quote_ident(required_columns.table_name))
                    and attname = required_columns.column_name
                    and attnum > 0
                    and not attisdropped))

    union all
    -- The shape every expand migration checks before it will run.
    select 9, 'postgres -> psychdeep_backend membership unwidened',
           (select count(*) from (select 1) as one
             where not coalesce((
                 select count(*) = 1
                    and bool_and(m.admin_option)
                    and not bool_or(m.inherit_option)
                    and not bool_or(m.set_option)
                   from pg_auth_members m
                  where m.roleid = to_regrole('psychdeep_backend')
                    and m.member = to_regrole('postgres')), false))

    union all
    -- A table here means DATABASE_URL is not carrying the search_path the
    -- deployment assumes, and PostgREST is exposing whatever landed instead.
    select 10, 'no application tables left in public',
           (select count(*) from pg_class relation
              join pg_namespace namespace on namespace.oid = relation.relnamespace
             where namespace.nspname = 'public'
               and relation.relkind = 'r'
               and relation.relname in (select table_name from expected_tables))
)
select check_name,
       case when failures = 0 then 'ok' else 'FAILED' end as status,
       failures as offending_rows
  from checks
 order by sort_key;
