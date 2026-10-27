import pytest

from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.ingest.schedule import SEASON_ENDPOINT
from nhl_forecast.ingest.validate import validate_games
from nhl_forecast.nhl_api.client import ApiResponse

pytestmark = pytest.mark.db

SEASON_LIST = {
    "data": [
        {
            "id": 20242025,
            "startDate": "2024-10-04T00:00:00",
            "regularSeasonEndDate": "2025-04-17T00:00:00",
            "endDate": "2025-06-17T00:00:00",
            "totalRegularSeasonGames": 3,
        }
    ]
}


@pytest.fixture
def season(db):
    store_raw(
        db,
        ApiResponse(
            source="nhl_stats",
            endpoint=SEASON_ENDPOINT,
            url="https://example.test/en/season",
            params={},
            http_status=200,
            payload=SEASON_LIST,
        ),
    )
    db.execute(
        "insert into teams (team_id, abbrev, name)"
        " values (901, 'AAA', 'A'), (902, 'BBB', 'B'), (903, 'CCC', 'C'), (904, 'DDD', 'D')"
    )
    for n, day, home, away in [(1, 5, 901, 902), (2, 5, 903, 904), (3, 6, 901, 903)]:
        insert_game(db, n, day, home, away)
    return db


def insert_game(db, n, day, home, away, state="OFF", score=(3, 2), period="REG", game_type=2):
    final = state == "OFF"
    db.execute(
        """
        insert into games (game_id, season, game_type, game_date, start_time_utc,
                           home_team_id, away_team_id, game_state,
                           home_score, away_score, last_period_type)
        values (%s, 20242025, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            2024000000 + game_type * 10000 + n,
            game_type,
            f"2024-10-{day:02d}",
            f"2024-10-{day:02d}T23:00:00Z",
            home,
            away,
            state,
            score[0] if final else None,
            score[1] if final else None,
            period if final else None,
        ),
    )


def failed(report):
    return {c.name: c.problems for c in report.checks if not c.ok}


def test_complete_season_passes(season):
    report = validate_games(season)

    assert failed(report) == {}
    assert report.seasons[0].games == report.seasons[0].expected == 3
    assert report.seasons[0].home_win_rate == 1.0


def test_missing_game_is_reported_by_number(season):
    season.execute("delete from games where game_id = 2024020002")

    assert failed(validate_games(season)) == {
        "regular-season games match the season list": (
            "20242025: 2 games, expected 3; missing #2; unexpected none",
        )
    }


def test_team_playing_twice_on_one_day(season):
    insert_game(season, 1, 5, 902, 901, game_type=3)

    assert "no team plays twice on one day" in failed(validate_games(season))


def test_overtime_win_by_two(season):
    season.execute(
        "update games set home_score = 4, away_score = 2, last_period_type = 'OT'"
        " where game_id = 2024020001"
    )

    assert failed(validate_games(season)) == {
        "every OT/SO game was won by exactly 1": ("2024020001 (2-4 OT)",)
    }


def test_past_game_still_scheduled(season):
    insert_game(season, 4, 7, 902, 904, state="FUT")

    problems = failed(validate_games(season))
    assert problems["games that started over 12 hours ago are final"] == ("2024020004 (FUT)",)
