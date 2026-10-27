#!/usr/bin/env bash
# Recreate the local test database from the migrations. Rerun after adding a migration.
set -euo pipefail
admin_url="${1:-postgresql://postgres:postgres@127.0.0.1:54322/postgres}"
test_db="${2:-nhl_test}"

PGOPTIONS="-c client_min_messages=warning" psql "$admin_url" -v ON_ERROR_STOP=1 -q \
    -c "drop database if exists $test_db with (force)" \
    -c "create database $test_db"

"$(dirname "$0")/apply_migrations_plain_postgres.sh" "${admin_url%/*}/$test_db"
echo "ready: ${admin_url%/*}/$test_db"
