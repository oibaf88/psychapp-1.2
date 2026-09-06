-- Local/offline database bootstrap. This runs only when the Docker volume is
-- created for the first time.

create schema if not exists psychdeep_v12 authorization psychapp;

alter role psychapp in database psychapp
    set search_path = psychdeep_v12, public;

-- SymmetricDS uses its own runtime tables. Keep them outside the application
-- schema so schema inspection and future migrations remain easy to reason about.
create schema if not exists psychdeep_sync authorization psychapp;
