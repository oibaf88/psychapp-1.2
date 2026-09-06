-- Run once in Supabase SQL Editor / DBeaver as the database owner BEFORE
-- starting the SymmetricDS cloud engine. This does not enable replication;
-- it only creates a private namespace for SymmetricDS runtime tables.

begin;

create schema if not exists psychdeep_sync;

revoke all on schema psychdeep_sync from public;
revoke all on schema psychdeep_sync from anon;
revoke all on schema psychdeep_sync from authenticated;
revoke all on schema psychdeep_sync from service_role;

-- SymmetricDS is expected to connect with an operator/database-owner account
-- during the first rollout so it can create its own sym_* tables and CDC
-- triggers on the explicitly allowlisted psychdeep_v12 tables.

commit;
