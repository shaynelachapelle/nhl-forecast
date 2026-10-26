-- Model registry, immutable live predictions, backtest predictions and ratings.

create table public.model_versions (
    version          text        primary key,  -- e.g. 'elo-v1'
    family           text        not null,     -- 'elo', 'logreg', 'gbm'
    description      text,
    hyperparams      jsonb       not null default '{}'::jsonb,
    feature_list     text[],
    training_seasons integer[],
    git_commit       text,
    artifact_path    text,
    backtest_metrics jsonb,
    -- New models start as shadow and are only promoted if they beat the primary.
    status           text        not null default 'shadow'
                     check (status in ('shadow', 'primary', 'retired')),
    created_at       timestamptz not null default now()
);

-- Live predictions. Insert-only.
create table public.predictions (
    prediction_id bigint           generated always as identity primary key,
    game_id       bigint           not null references public.games,
    model_version text             not null references public.model_versions,
    created_at    timestamptz      not null default now(),
    -- Strictly inside (0, 1) so log loss is always finite.
    p_home_win    double precision not null check (p_home_win > 0 and p_home_win < 1),
    features      jsonb            not null default '{}'::jsonb,
    run_id        uuid             not null  -- groups the predictions from one job run
);

create index predictions_game_model_created_idx
    on public.predictions (game_id, model_version, created_at desc);

-- Stamp created_at with the server clock (so it cannot be backdated) and
-- reject predictions for games that have already started.
create function public.predictions_check_pregame()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    v_start timestamptz;
begin
    new.created_at := now();
    select g.start_time_utc into v_start
    from public.games g
    where g.game_id = new.game_id;
    if new.created_at >= v_start then
        raise exception 'game % started at %; predictions must be made before puck drop',
            new.game_id, v_start;
    end if;
    return new;
end;
$$;

create trigger predictions_pregame
    before insert on public.predictions
    for each row execute function public.predictions_check_pregame();

create trigger predictions_append_only
    before update or delete on public.predictions
    for each row execute function public.forbid_mutation();

create trigger predictions_no_truncate
    before truncate on public.predictions
    for each statement execute function public.forbid_mutation();

-- Backtest predictions: derived from history, so rebuilding is fine. 
-- Kept apart from live predictions so the two can never be mixed up.
create table public.backtest_predictions (
    model_version text             not null references public.model_versions,
    game_id       bigint           not null references public.games,
    p_home_win    double precision not null check (p_home_win > 0 and p_home_win < 1),
    features      jsonb            not null default '{}'::jsonb,
    created_at    timestamptz      not null default now(),
    primary key (model_version, game_id)
);

-- Rating before and after each game. A team's rating on a date is the latest
-- rating_post before it; between-season regression shows up in the first
-- game's rating_pre.
create table public.ratings_history (
    model_version text             not null references public.model_versions,
    team_id       smallint         not null references public.teams,
    game_id       bigint           not null references public.games,
    as_of         timestamptz      not null,  -- the game's start_time_utc
    rating_pre    double precision not null,
    rating_post   double precision not null,
    primary key (model_version, team_id, game_id)
);

create index ratings_history_team_as_of_idx
    on public.ratings_history (model_version, team_id, as_of desc);
