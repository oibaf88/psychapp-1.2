-- PsychDeep 1.2: therapist <-> Agent 3 clinical copilot conversations.
--
-- Expand-only production migration. It adds one table and touches nothing
-- that already exists, so a running release keeps working until the new
-- application code is deployed.
--
-- These rows are professional-facing only: the patient never reads them and
-- nothing here feeds the deterministic risk engine. They are still clinical
-- data about an identified patient, so the table gets the same ownership and
-- FORCE RLS treatment as agent2_analysis_traces: owned by psychdeep_backend,
-- reachable only through the backend role, no privileges for public, anon,
-- authenticated or service_role.

begin;

-- Supabase grants postgres membership in psychdeep_backend with ADMIN=TRUE,
-- INHERIT=FALSE and SET=FALSE. Refuse to proceed if that precondition has
-- drifted, exactly as the previous migration does. The extra SET grant below
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

create table if not exists psychdeep_v12.therapist_copilot_messages (
    id uuid primary key,
    professional_id uuid not null references psychdeep_v12.users(id) on delete cascade,
    patient_id uuid not null references psychdeep_v12.users(id) on delete cascade,
    role varchar(16) not null,
    content text not null,
    kind varchar(24) not null default 'question',
    provider varchar(32),
    requested_model varchar(128),
    context_window_days integer,
    context_counts jsonb,
    error_kind varchar(64),
    created_at timestamp not null default (now() at time zone 'utc'),
    constraint ck_copilot_role check (role in ('user', 'assistant')),
    constraint ck_copilot_kind check (kind in ('question', 'answer', 'summary')),
    constraint ck_copilot_window check (context_window_days is null or context_window_days between 1 and 365)
);

create index if not exists ix_therapist_copilot_messages_professional_id
    on psychdeep_v12.therapist_copilot_messages(professional_id);
create index if not exists ix_therapist_copilot_messages_patient_id
    on psychdeep_v12.therapist_copilot_messages(patient_id);
create index if not exists ix_copilot_pair_created
    on psychdeep_v12.therapist_copilot_messages(professional_id, patient_id, created_at);

alter table psychdeep_v12.therapist_copilot_messages enable row level security;
alter table psychdeep_v12.therapist_copilot_messages force row level security;

drop policy if exists backend_full_access on psychdeep_v12.therapist_copilot_messages;
create policy backend_full_access
    on psychdeep_v12.therapist_copilot_messages
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.therapist_copilot_messages
    from public, anon, authenticated, service_role;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a half-hardened table.
do $$
declare
    membership_is_restored boolean;
    table_is_hardened boolean;
    policy_exists boolean;
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
       and relation.relname = 'therapist_copilot_messages';

    select exists (
        select 1 from pg_policies
         where schemaname = 'psychdeep_v12'
           and tablename = 'therapist_copilot_messages'
           and policyname = 'backend_full_access'
           and 'psychdeep_backend' = any(roles)
    ) into policy_exists;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'therapist_copilot_messages owner/RLS hardening is incomplete';
    end if;
    if not policy_exists then
        raise exception 'therapist_copilot_messages backend RLS policy is missing';
    end if;
end
$$;

commit;
