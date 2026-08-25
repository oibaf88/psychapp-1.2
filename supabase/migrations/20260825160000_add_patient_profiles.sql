-- PsychDeep 1.2: what is known about a patient, accumulated across sessions.
--
-- The analytic layer judged every patient against the same constants:
-- `rumination_score > 0.60` meant the same thing for someone who writes in
-- long anxious spirals and for someone who answers in four words. This table
-- is the other half of that comparison — who this person is, and what is
-- normal for them.
--
-- Expand-only. One new table, nothing altered, nothing backfilled. A patient
-- with no row here is evaluated exactly as before: that fallback is the
-- safety property, not a convenience, because a patient the system has not
-- met yet must not become un-assessable.
--
-- The table holds a model-written portrait of a patient in treatment, so it
-- gets the same hardening as the clinical tables: owned by psychdeep_backend,
-- FORCE RLS, a backend-only policy, and no privileges for public, anon,
-- authenticated or service_role.

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

create table if not exists psychdeep_v12.patient_profiles (
    id uuid primary key,
    -- One row per patient, enforced by the database. Two profiles would mean
    -- two answers to "what is normal for this person", and the engine would
    -- silently pick whichever came back first.
    user_id uuid not null unique references psychdeep_v12.users(id) on delete cascade,

    -- {"rumination_score": {"mean": .., "std": .., "n": ..}, ...}
    linguistic_baseline jsonb,
    linguistic_baseline_n integer not null default 0,
    linguistic_baseline_updated_at timestamp,

    portrait text,
    -- One step back, kept so a portrait that drifted can be compared with
    -- what it drifted from.
    previous_portrait text,
    portrait_version integer not null default 0,
    portrait_updated_at timestamp,
    -- Set when a clinician wrote it. The analyser may add to a hand-edited
    -- portrait; it is told never to contradict one.
    portrait_edited_by uuid references psychdeep_v12.users(id) on delete set null,

    -- [{"topic": .., "note": .., "opened_at": .., "source": ..}]
    open_threads jsonb,

    created_at timestamp not null default (now() at time zone 'utc'),
    updated_at timestamp not null default (now() at time zone 'utc'),

    constraint ck_patient_profile_portrait_version check (portrait_version >= 0),
    constraint ck_patient_profile_baseline_n check (linguistic_baseline_n >= 0)
);

create index if not exists ix_patient_profiles_user
    on psychdeep_v12.patient_profiles(user_id);

alter table psychdeep_v12.patient_profiles enable row level security;
alter table psychdeep_v12.patient_profiles force row level security;

drop policy if exists backend_full_access on psychdeep_v12.patient_profiles;
create policy backend_full_access
    on psychdeep_v12.patient_profiles
    for all
    to psychdeep_backend
    using (true)
    with check (true);

revoke all on table psychdeep_v12.patient_profiles
    from public, anon, authenticated, service_role;

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a half-hardened table
-- holding clinical prose.
do $$
declare
    membership_is_restored boolean;
    table_is_hardened boolean;
    policy_exists boolean;
    one_row_per_patient boolean;
    column_count integer;
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
       and relation.relname = 'patient_profiles';

    select exists (
        select 1 from pg_policies
         where schemaname = 'psychdeep_v12'
           and tablename = 'patient_profiles'
           and policyname = 'backend_full_access'
           and 'psychdeep_backend' = any(roles)
    ) into policy_exists;

    select exists (
        select 1
          from pg_constraint
         where conrelid = 'psychdeep_v12.patient_profiles'::regclass
           and contype = 'u'
           and conkey = array[
               (select attnum from pg_attribute
                 where attrelid = 'psychdeep_v12.patient_profiles'::regclass
                   and attname = 'user_id')
           ]
    ) into one_row_per_patient;

    -- pg_catalog, not information_schema: the latter only shows columns the
    -- caller has privileges on, and postgres deliberately has none on these
    -- backend-owned tables.
    select count(*) into column_count
      from pg_attribute
     where attrelid = 'psychdeep_v12.patient_profiles'::regclass
       and attnum > 0
       and not attisdropped;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not coalesce(table_is_hardened, false) then
        raise exception 'patient_profiles owner/RLS hardening is incomplete';
    end if;
    if not policy_exists then
        raise exception 'patient_profiles backend RLS policy is missing';
    end if;
    if not one_row_per_patient then
        raise exception 'patient_profiles is missing the one-row-per-patient constraint';
    end if;
    if column_count <> 13 then
        raise exception 'patient_profiles column set is incomplete (found %)', column_count;
    end if;
end
$$;

commit;
