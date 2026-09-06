-- PsychDeep local <-> cloud replication policy for SymmetricDS.
--
-- Run this ONLY on the SymmetricDS *cloud engine* after it has initialized its
-- psychdeep_sync tables. Do not run it as a Supabase migration: replication is
-- an operator-controlled feature and must remain opt-in.
--
-- Direction is intentionally edge-friendly:
--   local -> cloud : P (the laptop pushes its queued changes)
--   cloud -> local : W (cloud waits; the laptop pulls when online)
-- No inbound PostgreSQL port is required on the laptop.

begin;

insert into psychdeep_sync.sym_node_group
    (node_group_id, description, create_time, last_update_time)
values
    ('cloud', 'PsychDeep hosted Supabase database', current_timestamp, current_timestamp),
    ('local', 'PsychDeep offline laptop database', current_timestamp, current_timestamp)
on conflict (node_group_id) do update
set description = excluded.description,
    last_update_time = current_timestamp;

insert into psychdeep_sync.sym_node_group_link
    (source_node_group_id, target_node_group_id, data_event_action, sync_config_enabled, create_time, last_update_time)
values
    ('local', 'cloud', 'P', 1, current_timestamp, current_timestamp),
    ('cloud', 'local', 'W', 1, current_timestamp, current_timestamp)
on conflict (source_node_group_id, target_node_group_id) do update
set data_event_action = excluded.data_event_action,
    sync_config_enabled = excluded.sync_config_enabled,
    last_update_time = current_timestamp;

insert into psychdeep_sync.sym_router
    (router_id, source_node_group_id, target_node_group_id, router_type, create_time, last_update_time)
values
    ('local_to_cloud', 'local', 'cloud', 'default', current_timestamp, current_timestamp),
    ('cloud_to_local', 'cloud', 'local', 'default', current_timestamp, current_timestamp)
on conflict (router_id) do update
set source_node_group_id = excluded.source_node_group_id,
    target_node_group_id = excluded.target_node_group_id,
    router_type = excluded.router_type,
    last_update_time = current_timestamp;

-- Explicit allowlist. Runtime credentials/configuration are deliberately not
-- present here: password_reset_tokens and llm_endpoint_configs never replicate.
-- SymmetricDS's own psychdeep_sync.* tables are also outside this list.
with replicated(table_name, load_order) as (
    values
      ('users', 10),
      ('user_consents', 20),
      ('patient_professional_assignments', 20),
      ('confirmed_facts', 30),
      ('baselines', 30),
      ('check_ins', 30),
      ('diary_entries', 30),
      ('safety_plans', 30),
      ('chat_messages', 30),
      ('patient_profiles', 35),
      ('agent2_analysis_traces', 40),
      ('alfa_signals', 50),
      ('psychosocial_observations', 50),
      ('risk_assessments', 60),
      ('professional_alerts', 70),
      ('notifications', 80),
      ('therapist_copilot_messages', 80),
      ('audit_log', 90),
      ('llm_usage_events', 90)
)
insert into psychdeep_sync.sym_trigger
    (trigger_id, source_schema_name, source_table_name, channel_id,
     sync_on_insert, sync_on_update, sync_on_delete, sync_on_incoming_batch,
     create_time, last_update_time, description)
select
    'psychdeep_' || table_name,
    'psychdeep_v12',
    table_name,
    'default',
    1, 1, 1, 0,
    current_timestamp, current_timestamp,
    'PsychDeep allowlisted offline sync table'
from replicated
on conflict (trigger_id) do update
set source_schema_name = excluded.source_schema_name,
    source_table_name = excluded.source_table_name,
    sync_on_insert = 1,
    sync_on_update = 1,
    sync_on_delete = 1,
    sync_on_incoming_batch = 0,
    last_update_time = current_timestamp;

with replicated(table_name, load_order) as (
    values
      ('users', 10), ('user_consents', 20), ('patient_professional_assignments', 20),
      ('confirmed_facts', 30), ('baselines', 30), ('check_ins', 30),
      ('diary_entries', 30), ('safety_plans', 30), ('chat_messages', 30),
      ('patient_profiles', 35), ('agent2_analysis_traces', 40), ('alfa_signals', 50),
      ('psychosocial_observations', 50), ('risk_assessments', 60),
      ('professional_alerts', 70), ('notifications', 80),
      ('therapist_copilot_messages', 80), ('audit_log', 90), ('llm_usage_events', 90)
), links as (
    select 'local_to_cloud'::varchar as router_id
    union all
    select 'cloud_to_local'::varchar
)
insert into psychdeep_sync.sym_trigger_router
    (trigger_id, router_id, enabled, initial_load_order, ping_back_enabled,
     create_time, last_update_time, description)
select
    'psychdeep_' || r.table_name,
    l.router_id,
    1,
    r.load_order,
    0,
    current_timestamp,
    current_timestamp,
    'PsychDeep bidirectional offline sync'
from replicated r
cross join links l
on conflict (trigger_id, router_id) do update
set enabled = 1,
    initial_load_order = excluded.initial_load_order,
    ping_back_enabled = 0,
    last_update_time = current_timestamp;

commit;
