-- GitHub starts scheduled workflow runs hours late, but runs requested through its API
-- start within seconds. So the database's own scheduler (pg_cron) asks GitHub to run the
-- daily workflow; the workflow's schedule stays as a backup.
--
-- Needs a fine-grained GitHub token (this repo only, Actions: read and write) stored in
-- Supabase Vault as 'github_dispatch_token'. Without that secret the call does nothing, so
-- local Supabase never triggers the real workflow.

-- Not exposed through the Supabase API, unlike public.
create schema if not exists ops;
revoke all on schema ops from public;

create function ops.dispatch_daily_workflow()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_token text;
begin
    select decrypted_secret into v_token
    from vault.decrypted_secrets
    where name = 'github_dispatch_token';
    if v_token is null then
        return null;
    end if;
    return net.http_post(
        url := 'https://api.github.com/repos/shaynelachapelle/nhl-forecast/actions/workflows/daily.yml/dispatches',
        body := jsonb_build_object('ref', 'main'),
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || v_token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'nhl-forecast-scheduler'
        )
    );
end;
$$;

revoke all on function ops.dispatch_daily_workflow() from public, anon, authenticated;

-- pg_cron and pg_net exist on Supabase but not on the plain Postgres used in CI, and pg_cron
-- only installs in its own database ('postgres'), not in a test database on the same server.
do $$
begin
    if exists (select from pg_available_extensions where name = 'pg_cron')
       and exists (select from pg_available_extensions where name = 'pg_net')
       and current_database() = coalesce(current_setting('cron.database_name', true), 'postgres') then
        create extension if not exists pg_net;
        create extension if not exists pg_cron;
        -- UTC: after the last West Coast games end, and a second try before afternoon games.
        perform cron.schedule('dispatch-daily-morning', '17 9 * * *', 'select ops.dispatch_daily_workflow()');
        perform cron.schedule('dispatch-daily-midday', '47 15 * * *', 'select ops.dispatch_daily_workflow()');
    end if;
end;
$$;
