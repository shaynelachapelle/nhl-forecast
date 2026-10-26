#!/usr/bin/env bash
set -euo pipefail
url="${1:?usage: $0 DATABASE_URL}"

psql "$url" -v ON_ERROR_STOP=1 -q <<'SQL'
do $$
begin
    if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
SQL

for f in supabase/migrations/*.sql; do
    echo "applying $f"
    psql "$url" -v ON_ERROR_STOP=1 -q -f "$f"
done
