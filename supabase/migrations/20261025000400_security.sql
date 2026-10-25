-- Supabase exposes the public schema over its REST API to the anon and
-- authenticated roles. Until there is a UI, nothing should be reachable that
-- way: jobs connect directly to Postgres as the postgres role, which bypasses
-- RLS. Enabling RLS with no policies denies those API roles everything.
-- Read-only policies get added when the UI needs them.

alter table public.raw_api_responses    enable row level security;
alter table public.teams                enable row level security;
alter table public.team_seasons         enable row level security;
alter table public.rating_lineage       enable row level security;
alter table public.games                enable row level security;
alter table public.model_versions       enable row level security;
alter table public.predictions          enable row level security;
alter table public.backtest_predictions enable row level security;
alter table public.ratings_history      enable row level security;

-- remove table privileges from the API roles.
revoke all on all tables in schema public from anon, authenticated;

-- Every future table needs its own "enable row level security" line
