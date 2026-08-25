#!/usr/bin/env bash
#
# Apply every PsychDeep migration to a Supabase project, in order, then run the
# readiness check. This is the step that has to happen BEFORE the Render deploy:
# the API verifies the schema contract at startup and refuses to serve a
# half-migrated database, so Render would keep the previous instance alive.
#
# Usage:
#   SUPABASE_DB_URL='postgresql://postgres:PASSWORD@HOST:5432/postgres?sslmode=require' \
#     supabase/deploy.sh
#
#   supabase/deploy.sh --dry-run     # list what would be applied, connect to nothing
#
# Connect as the project's `postgres` user (Supabase Dashboard -> Connect ->
# Session pooler, or the direct host from a machine with IPv6). The migrations
# take the psychdeep_backend role temporarily and hand it back before they
# commit, which needs postgres, not the backend role the API itself uses.
#
# Every file is idempotent and wrapped in its own transaction: re-running is a
# no-op, and a failure rolls that file back rather than leaving the schema half
# applied.
set -euo pipefail

migrations_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/migrations"
verify_sql="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/verify.sql"

# Filename order is apply order: the bootstrap is 00000000000000 and the expand
# migrations carry their release timestamps.
mapfile -t files < <(find "$migrations_dir" -maxdepth 1 -name '*.sql' | sort)

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No migrations found in $migrations_dir" >&2
    exit 1
fi

if [[ "${1:-}" == "--dry-run" ]]; then
    echo "Would apply, in this order:"
    printf '  %s\n' "${files[@]##*/}"
    exit 0
fi

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
    echo "SUPABASE_DB_URL is not set. See the header of this script." >&2
    exit 1
fi

for file in "${files[@]}"; do
    echo "==> ${file##*/}"
    psql "$SUPABASE_DB_URL" --set ON_ERROR_STOP=1 --quiet --no-psqlrc --file "$file"
done

echo
echo "==> readiness check (every row must read 'ok')"
psql "$SUPABASE_DB_URL" --no-psqlrc --file "$verify_sql"
