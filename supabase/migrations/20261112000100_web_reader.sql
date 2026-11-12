-- Read-only login for the website. Its password is set outside migrations:
--   alter role web_reader password '...';
-- Roles are cluster-wide, so a test database rebuilt on the same server may already have it.
do $$
begin
    if not exists (select from pg_roles where rolname = 'web_reader') then
        create role web_reader login;
    end if;
end $$;

alter role web_reader set default_transaction_read_only = on;
alter role web_reader set statement_timeout = '10s';

grant usage on schema public to web_reader;
grant select on
    public.teams, public.team_seasons, public.games, public.model_versions,
    public.predictions, public.ratings_history, public.job_runs
to web_reader;

-- RLS stays on. These policies name only web_reader, so the API roles still read nothing.
create policy web_reader_read on public.teams           for select to web_reader using (true);
create policy web_reader_read on public.team_seasons    for select to web_reader using (true);
create policy web_reader_read on public.games           for select to web_reader using (true);
create policy web_reader_read on public.model_versions  for select to web_reader using (true);
create policy web_reader_read on public.predictions     for select to web_reader using (true);
create policy web_reader_read on public.ratings_history for select to web_reader using (true);
create policy web_reader_read on public.job_runs        for select to web_reader using (true);
