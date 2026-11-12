from datetime import date

import httpx
import pytest

from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.ingest.standings import (
    STANDINGS_ENDPOINT,
    fetch_standings,
    load_team_seasons,
    standings_date,
)
from nhl_forecast.nhl_api.client import ApiResponse, NhlClient
from nhl_forecast.nhl_api.parse import Season


def season(year: int) -> Season:
    return Season(
        season=year * 10000 + year + 1,
        start_date=date(year, 10, 4),
        regular_season_end_date=date(year + 1, 4, 17),
        end_date=date(year + 1, 6, 17),
        regular_season_games=1312,
    )


def standings(season_id, *teams):
    return {
        "standings": [
            {
                "seasonId": season_id,
                "teamAbbrev": {"default": abbrev},
                "conferenceName": "Western",
                "divisionName": division,
            }
            for abbrev, division in teams
        ]
    }


def store(db, day, payload):
    store_raw(
        db,
        ApiResponse(
            source="nhl_web",
            endpoint=STANDINGS_ENDPOINT,
            url=f"https://example.test/v1/standings/{day}",
            params={"date": day},
            http_status=200,
            payload=payload,
        ),
    )


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2024, 10, 4), None),
        (date(2024, 11, 10), date(2024, 11, 10)),
        (date(2025, 9, 1), date(2025, 4, 17)),
    ],
)
def test_standings_date(today, expected):
    assert standings_date(season(2024), today) == expected


@pytest.mark.db
def test_fetch_skips_stored_seasons_and_survives_a_failed_request(db):
    store(db, "2023-04-14", standings(20222023, ("TOR", "Atlantic")))
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        day = request.url.path.rsplit("/", 1)[-1]
        requested.append(day)
        if day == "2024-04-17":
            return httpx.Response(404)
        return httpx.Response(200, json=standings(20242025, ("TOR", "Atlantic")))

    client = NhlClient(transport=httpx.MockTransport(handler), min_interval=0)
    fetched = fetch_standings(
        db, client, [season(2022), season(2023), season(2024)], date(2024, 11, 10)
    )

    assert fetched == 1
    assert requested == ["2024-04-17", "2024-11-10"]


@pytest.mark.db
def test_load_matches_abbreviations_through_each_seasons_games(db):
    db.execute(
        "insert into teams (team_id, abbrev, name)"
        " values (10, 'TOR', 'Toronto'), (59, 'UTA', 'Utah HC'), (68, 'UTA', 'Utah Mammoth')"
    )
    for game_id, season_id, away in ((2024020001, 20242025, 59), (2025020001, 20252026, 68)):
        db.execute(
            """
            insert into games (game_id, season, game_type, game_date, start_time_utc,
                               home_team_id, away_team_id, game_state)
            values (%s, %s, 2, '2024-10-10', '2024-10-10T23:00:00Z', 10, %s, 'FUT')
            """,
            (game_id, season_id, away),
        )
    store(db, "2025-04-17", standings(20242025, ("TOR", "Atlantic"), ("UTA", "Central")))
    store(db, "2025-11-10", standings(20252026, ("UTA", "Central"), ("XXX", "Pacific")))

    assert load_team_seasons(db) == 3
    rows = db.execute(
        "select team_id, season, division from team_seasons order by season, team_id"
    ).fetchall()
    assert [tuple(r.values()) for r in rows] == [
        (10, 20242025, "Atlantic"),
        (59, 20242025, "Central"),
        (68, 20252026, "Central"),
    ]
    assert load_team_seasons(db) == 0
