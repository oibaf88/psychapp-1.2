begin;

-- The clinical tables are owned by psychdeep_backend. Use the same temporary
-- SET-capable membership pattern as the rest of the production migrations.
grant psychdeep_backend to postgres with set true;
set local role psychdeep_backend;

create index if not exists ix_alfa_signals_user_id
    on psychdeep_v12.alfa_signals(user_id);
create index if not exists ix_baselines_user_id
    on psychdeep_v12.baselines(user_id);
create index if not exists ix_chat_messages_user_id
    on psychdeep_v12.chat_messages(user_id);
create index if not exists ix_check_ins_user_id
    on psychdeep_v12.check_ins(user_id);
create index if not exists ix_confirmed_facts_user_id
    on psychdeep_v12.confirmed_facts(user_id);
create index if not exists ix_diary_entries_user_id
    on psychdeep_v12.diary_entries(user_id);
create index if not exists ix_llm_endpoint_configs_created_by
    on psychdeep_v12.llm_endpoint_configs(created_by);
create index if not exists ix_notifications_professional_id
    on psychdeep_v12.notifications(professional_id);
create index if not exists ix_notifications_user_id
    on psychdeep_v12.notifications(user_id);
create index if not exists ix_password_reset_tokens_user_id
    on psychdeep_v12.password_reset_tokens(user_id);
create index if not exists ix_patient_prof_assign_patient_id
    on psychdeep_v12.patient_professional_assignments(patient_id);
create index if not exists ix_patient_prof_assign_professional_id
    on psychdeep_v12.patient_professional_assignments(professional_id);
create index if not exists ix_patient_profiles_portrait_edited_by
    on psychdeep_v12.patient_profiles(portrait_edited_by);
create index if not exists ix_professional_alerts_user_id
    on psychdeep_v12.professional_alerts(user_id);
create index if not exists ix_risk_assessments_user_id
    on psychdeep_v12.risk_assessments(user_id);
create index if not exists ix_user_consents_user_id
    on psychdeep_v12.user_consents(user_id);

reset role;
revoke psychdeep_backend from postgres granted by postgres;

commit;
