-- Each model's official forecast per game: its latest prediction made before puck drop.
-- The one definition shared by scoring (score_live) and the website. For a game that hasn't
-- started yet it is the current forecast, which can still change before puck drop.
-- security_invoker makes the view run with the caller's rights, so RLS on predictions applies.
create view public.official_predictions with (security_invoker = true) as
select distinct on (p.model_version, p.game_id)
       p.prediction_id, p.game_id, p.model_version, p.created_at, p.p_home_win, p.features, p.run_id
from public.predictions p
join public.games g using (game_id)
where p.created_at < g.start_time_utc
order by p.model_version, p.game_id, p.created_at desc, p.prediction_id desc;

revoke all on public.official_predictions from anon, authenticated;
grant select on public.official_predictions to web_reader;
