-- Teams and games, parsed from raw_api_responses. Rebuildable at any time.

create table public.teams (
    team_id      smallint    primary key,  -- NHL team id (api-web homeTeam.id)
    abbrev       text        not null,     -- not unique over time: UTA is both 59 and 68
    name         text        not null,
    franchise_id smallint,                 -- from stats API /en/team
    updated_at   timestamptz not null default now()
);

create trigger teams_set_updated_at
    before update on public.teams
    for each row execute function public.set_updated_at();

-- Conference and division by season: realignment in 2021-22, and the
-- temporary 2020-21 divisions, mean these are not fixed attributes of a team.
create table public.team_seasons (
    team_id    smallint not null references public.teams,
    season     integer  not null,
    conference text,
    division   text,
    primary key (team_id, season)
);

-- Which team's rating a team inherits at its first season. A modelling
-- choice, not an API fact: the NHL treats Utah as a new franchise (40, not
-- Arizona's 28), and the Hockey Club -> Mammoth rename got a new team id.
-- No foreign keys because these rows exist before any team is ingested.
create table public.rating_lineage (
    team_id             smallint primary key,
    predecessor_team_id smallint not null,
    first_season        integer  not null,
    note                text     not null,
    check (team_id <> predecessor_team_id)
);

insert into public.rating_lineage (team_id, predecessor_team_id, first_season, note) values
    (59, 53, 20242025, 'Utah Hockey Club took over the Arizona Coyotes roster'),
    (68, 59, 20252026, 'Utah Hockey Club renamed Utah Mammoth under a new team id');

create table public.games (
    game_id          bigint      primary key,  -- e.g. 2025020022
    season           integer     not null,     -- e.g. 20252026
    game_type        smallint    not null,     -- 1 preseason, 2 regular, 3 playoffs
    game_date        date        not null,     -- local date at the venue
    start_time_utc   timestamptz not null,     -- puck drop; predictions must precede it
    home_team_id     smallint    not null references public.teams,
    away_team_id     smallint    not null references public.teams,
    neutral_site     boolean     not null default false,  -- e.g. Global Series in Europe
    venue_timezone   text,                                -- e.g. 'America/Winnipeg'
    game_state       text        not null,     -- FUT, PRE, LIVE, CRIT, FINAL, OFF
    home_score       smallint,
    away_score       smallint,
    last_period_type text,                     -- REG, OT or SO once final
    -- Defined once here so every query agrees on who won.
    home_win         boolean generated always as (
        case when game_state in ('FINAL', 'OFF') then home_score > away_score end
    ) stored,
    source_raw_id    bigint      references public.raw_api_responses,  -- lineage
    updated_at       timestamptz not null default now(),

    check (home_team_id <> away_team_id),
    check (season / 10000 + 1 = season % 10000),
    check (last_period_type in ('REG', 'OT', 'SO')),
    check (game_state not in ('FINAL', 'OFF')
           or (home_score is not null and away_score is not null))
);

create index games_season_type_start_idx on public.games (season, game_type, start_time_utc);
create index games_date_idx on public.games (game_date);

create trigger games_set_updated_at
    before update on public.games
    for each row execute function public.set_updated_at();
