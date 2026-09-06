begin;

-- SymmetricDS gets its own role. It remains NOLOGIN until an operator creates
-- a random password out-of-band; credentials never belong in migrations/Git.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'psychdeep_sync') then
        create role psychdeep_sync nologin noinherit connection limit 5;
    else
        alter role psychdeep_sync nologin noinherit connection limit 5;
    end if;
end
$$;

create schema if not exists psychdeep_sync;
revoke all on schema psychdeep_sync from public;

do $$
declare
    role_name text;
begin
    foreach role_name in array array['anon','authenticated','service_role','psychdeep_backend'] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('revoke all on schema psychdeep_sync from %I', role_name);
        end if;
    end loop;
end
$$;

grant usage, create on schema psychdeep_sync to psychdeep_sync;
grant usage on schema psychdeep_v12 to psychdeep_sync;

-- Application tables are owned by psychdeep_backend. Temporarily SET ROLE so
-- grants and RLS policies are created by the owner, then remove that temporary
-- membership exactly as the other production migrations do.
grant psychdeep_backend to postgres with set true;
set local role psychdeep_backend;

grant select, insert, update, delete, trigger on
    psychdeep_v12.users,
    psychdeep_v12.user_consents,
    psychdeep_v12.patient_professional_assignments,
    psychdeep_v12.confirmed_facts,
    psychdeep_v12.baselines,
    psychdeep_v12.check_ins,
    psychdeep_v12.diary_entries,
    psychdeep_v12.safety_plans,
    psychdeep_v12.chat_messages,
    psychdeep_v12.patient_profiles,
    psychdeep_v12.agent2_analysis_traces,
    psychdeep_v12.alfa_signals,
    psychdeep_v12.psychosocial_observations,
    psychdeep_v12.risk_assessments,
    psychdeep_v12.professional_alerts,
    psychdeep_v12.notifications,
    psychdeep_v12.therapist_copilot_messages,
    psychdeep_v12.audit_log,
    psychdeep_v12.llm_usage_events
    to psychdeep_sync;

-- Runtime/authentication configuration is deliberately node-local and must
-- never be copied by the replication engine.
revoke all privileges on psychdeep_v12.llm_endpoint_configs from psychdeep_sync;
revoke all privileges on psychdeep_v12.password_reset_tokens from psychdeep_sync;

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'users','user_consents','patient_professional_assignments','confirmed_facts',
        'baselines','check_ins','diary_entries','safety_plans','chat_messages',
        'patient_profiles','agent2_analysis_traces','alfa_signals',
        'psychosocial_observations','risk_assessments','professional_alerts',
        'notifications','therapist_copilot_messages','audit_log','llm_usage_events'
    ] loop
        execute format('drop policy if exists sync_replication_access on psychdeep_v12.%I', table_name);
        execute format(
            'create policy sync_replication_access on psychdeep_v12.%I for all to psychdeep_sync using (true) with check (true)',
            table_name
        );
    end loop;
end
$$;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail closed if either excluded table accidentally becomes readable or the
-- allowlist does not have exactly one policy per replicated table.
do $$
declare
    policy_count integer;
begin
    if has_table_privilege('psychdeep_sync', 'psychdeep_v12.llm_endpoint_configs', 'SELECT')
       or has_table_privilege('psychdeep_sync', 'psychdeep_v12.password_reset_tokens', 'SELECT') then
        raise exception 'sync role can read an excluded runtime/auth table';
    end if;

    select count(*) into policy_count
    from pg_policies
    where schemaname = 'psychdeep_v12'
      and policyname = 'sync_replication_access';

    if policy_count <> 19 then
        raise exception 'expected 19 sync policies, found %', policy_count;
    end if;
end
$$;

commit;
