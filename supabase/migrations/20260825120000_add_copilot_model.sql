-- PsychDeep 1.2: a model setting of its own for Agent 3, the clinical
-- copilot.
--
-- Expand-only. One nullable column on `llm_endpoint_configs`.
--
-- Why nullable rather than NOT NULL DEFAULT chat_model: NULL already means
-- something exact here — "the copilot uses whatever the conversational agent
-- uses", which is precisely what every row written before this column
-- existed meant. Backfilling chat_model into old rows would turn an absence
-- into a decision nobody made, and this table exists so that "what was
-- serving the app in March" stays answerable. `llm_config._from_row` applies
-- the fallback on read, so behaviour is identical either way.
--
-- Same hardening as the rest of the table's history: the work runs as
-- psychdeep_backend, and the pre/post assertions refuse to leave the
-- temporary role membership behind.

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

alter table psychdeep_v12.llm_endpoint_configs
    add column if not exists copilot_model varchar(160);

reset role;
revoke psychdeep_backend from postgres granted by postgres;

-- Fail the whole transaction rather than commit a half-applied change.
do $$
declare
    membership_is_restored boolean;
    column_exists boolean;
    column_is_nullable boolean;
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

    -- pg_catalog, not information_schema: the latter only shows columns the
    -- caller has privileges on, and postgres deliberately has none on these
    -- backend-owned tables, so it would report a successful ALTER as missing.
    select exists (
        select 1 from pg_attribute
         where attrelid = 'psychdeep_v12.llm_endpoint_configs'::regclass
           and attname = 'copilot_model'
           and attnum > 0
           and not attisdropped
    ) into column_exists;

    select not attnotnull into column_is_nullable
      from pg_attribute
     where attrelid = 'psychdeep_v12.llm_endpoint_configs'::regclass
       and attname = 'copilot_model'
       and attnum > 0
       and not attisdropped;

    if not coalesce(membership_is_restored, false) then
        raise exception 'Temporary psychdeep_backend membership was not cleaned up';
    end if;
    if not column_exists then
        raise exception 'llm_endpoint_configs.copilot_model was not added';
    end if;
    if not coalesce(column_is_nullable, false) then
        raise exception 'llm_endpoint_configs.copilot_model must stay nullable (NULL means "same as chat")';
    end if;
end
$$;

commit;
