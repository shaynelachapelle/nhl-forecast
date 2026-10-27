from datetime import UTC, date, datetime

import pytest

from nhl_forecast.nhl_api.parse import Game, Season, parse_schedule_week, parse_seasons

WEEKS = [
    "web_schedule_2024-10-04.json",
    "web_schedule_2025-10-11.json",
    "web_schedule_2026-10-26.json",
]


def minimal_week(**overrides):
    side = {"placeName": {"default": "X"}, "commonName": {"default": "Y"}}
    game = {
        "id": 2024030111,
        "season": 20242025,
        "gameType": 3,
        "gameScheduleState": "OK",
        "startTimeUTC": "2025-04-20T23:00:00Z",
        "neutralSite": False,
        "venueTimezone": "America/Toronto",
        "gameState": "FUT",
        "homeTeam": {"id": 10, "abbrev": "TOR", **side},
        "awayTeam": {"id": 9, "abbrev": "OTT", **side},
    } | overrides
    return {"gameWeek": [{"date": "2025-04-20", "games": [game]}]}


def test_final_game_maps_every_field(load_fixture):
    week = parse_schedule_week(load_fixture("web_schedule_2025-10-11.json"))
    game = next(g for g in week.games if g.game_id == 2025020022)

    assert game == Game(
        game_id=2025020022,
        season=20252026,
        game_type=2,
        game_date=date(2025, 10, 11),
        start_time_utc=datetime(2025, 10, 11, 17, 30, tzinfo=UTC),
        home_team_id=52,
        away_team_id=26,
        neutral_site=False,
        venue_timezone="America/Winnipeg",
        game_state="OFF",
        home_score=3,
        away_score=2,
        last_period_type="REG",
    )
    assert len(week.games) == 54


def test_shootouts_are_flagged(load_fixture):
    week = parse_schedule_week(load_fixture("web_schedule_2025-10-11.json"))

    assert sorted(g.game_id for g in week.games if g.last_period_type == "SO") == [
        2025020034,
        2025020047,
        2025020063,
        2025020074,
    ]


@pytest.mark.parametrize("fixture", WEEKS)
def test_final_games_have_scores_and_ot_so_margin_of_one(load_fixture, fixture):
    for g in parse_schedule_week(load_fixture(fixture)).games:
        if g.game_state in ("OFF", "FINAL"):
            assert g.home_score is not None and g.away_score is not None
            assert g.last_period_type in ("REG", "OT", "SO")
            if g.last_period_type != "REG":
                assert abs(g.home_score - g.away_score) == 1


def test_unstarted_games_have_no_result(load_fixture):
    week = parse_schedule_week(load_fixture("web_schedule_2026-10-26.json"))
    future = [g for g in week.games if g.game_state == "FUT"]

    assert future
    assert all(
        g.home_score is None and g.away_score is None and g.last_period_type is None for g in future
    )


def test_preseason_and_cancelled_games_are_dropped(load_fixture):
    week = parse_schedule_week(load_fixture("web_schedule_2024-10-04.json"))

    assert len(week.games) == 19
    assert {g.game_type for g in week.games} == {2}


def test_neutral_site_games(load_fixture):
    week = parse_schedule_week(load_fixture("web_schedule_2024-10-04.json"))
    prague = {g.game_id: g for g in week.games if g.neutral_site}

    assert set(prague) == {2024020001, 2024020002}
    assert {g.venue_timezone for g in prague.values()} == {"Europe/Prague"}


def test_playoff_games_are_kept():
    week = parse_schedule_week(minimal_week())

    assert [g.game_type for g in week.games] == [3]


def test_cancelled_regular_season_game_is_dropped():
    week = parse_schedule_week(minimal_week(gameType=2, gameScheduleState="CNCL"))

    assert week.games == ()


@pytest.mark.parametrize(
    ("fixture", "team_id", "name"),
    [
        ("web_schedule_2025-10-11.json", 52, "Winnipeg Jets"),
        ("web_schedule_2024-10-04.json", 59, "Utah Hockey Club"),
        ("web_schedule_2024-10-04.json", 8, "Montréal Canadiens"),
        ("web_schedule_2026-10-26.json", 68, "Utah Mammoth"),
    ],
)
def test_team_names(load_fixture, fixture, team_id, name):
    teams = {t.team_id: t for t in parse_schedule_week(load_fixture(fixture)).teams}

    assert teams[team_id].name == name


def test_teams_are_listed_once(load_fixture):
    teams = parse_schedule_week(load_fixture("web_schedule_2025-10-11.json")).teams

    assert len(teams) == len({t.team_id for t in teams}) == 32


def test_seasons_have_walk_dates_and_game_counts(load_fixture):
    seasons = {s.season: s for s in parse_seasons(load_fixture("stats_season.json"))}

    assert seasons[20192020] == Season(
        season=20192020,
        start_date=date(2019, 10, 2),
        regular_season_end_date=date(2020, 3, 11),
        end_date=date(2020, 9, 28),
        regular_season_games=1082,
    )


def test_season_list_matches_known_game_counts(load_fixture):
    seasons = {s.season: s for s in parse_seasons(load_fixture("stats_season.json"))}
    known = {20152016: 1230, 20162017: 1230, 20172018: 1271, 20182019: 1271}
    known |= {20192020: 1082, 20202021: 868, 20262027: 1344}
    known |= {season: 1312 for season in range(20212022, 20262027, 10001)}

    assert {s: seasons[s].regular_season_games for s in known} == known
