from datetime import date, timedelta

import httpx
import pytest

from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.ingest.schedule import (
    SCHEDULE_ENDPOINT,
    fetch_schedule,
    load_schedules,
    week_is_complete,
)
from nhl_forecast.nhl_api.client import ApiResponse, NhlClient
from nhl_forecast.nhl_api.parse import Season

SEASON = Season(
    season=20992100,
    start_date=date(2099, 10, 1),
    regular_season_end_date=date(2099, 10, 15),
    end_date=date(2099, 10, 15),
    regular_season_games=3,
)
TODAY = date(2099, 10, 20)


def game(game_id, state="OFF", home_score=3, away_score=2):
    def side(team_id, score):
        s = {
            "id": team_id,
            "abbrev": f"T{team_id}",
            "placeName": {"default": "Place"},
            "commonName": {"default": f"Club{team_id}"},
        }
        if state not in ("FUT", "PRE"):
            s["score"] = score
        return s

    g = {
        "id": game_id,
        "season": 20992100,
        "gameType": 2,
        "gameScheduleState": "OK",
        "startTimeUTC": "2099-10-01T23:00:00Z",
        "neutralSite": False,
        "venueTimezone": "America/Toronto",
        "gameState": state,
        "homeTeam": side(901, home_score),
        "awayTeam": side(902, away_score),
    }
    if state == "OFF":
        g["gameOutcome"] = {"lastPeriodType": "REG"}
    return g


def week(start, games=(), next_start=None):
    first = date.fromisoformat(start)
    days = [{"date": (first + timedelta(days=i)).isoformat(), "games": []} for i in range(7)]
    days[0]["games"] = list(games)
    return {"gameWeek": days, "nextStartDate": next_start}


def fake_nhl(weeks):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        day = request.url.path.rsplit("/", 1)[-1]
        requested.append(day)
        return httpx.Response(200, json=weeks[day])

    return NhlClient(transport=httpx.MockTransport(handler), min_interval=0), requested


def store_week(db, payload, day):
    return store_raw(
        db,
        ApiResponse(
            source="nhl_web",
            endpoint=SCHEDULE_ENDPOINT,
            url=f"https://example.test/v1/schedule/{day}",
            params={"date": day},
            http_status=200,
            payload=payload,
        ),
    )


def test_week_is_complete_once_its_days_are_over_and_kept_games_final(load_fixture):
    final_week = load_fixture("web_schedule_2025-10-11.json")
    future_week = load_fixture("web_schedule_2026-10-26.json")
    with_cancelled_preseason = load_fixture("web_schedule_2024-10-04.json")

    assert week_is_complete(final_week, today=date(2026, 10, 26))
    assert not week_is_complete(final_week, today=date(2025, 10, 15))
    assert not week_is_complete(future_week, today=date(2027, 1, 1))
    assert week_is_complete(with_cancelled_preseason, today=date(2026, 10, 26))


@pytest.mark.db
def test_second_run_only_refetches_incomplete_weeks(db):
    client, requested = fake_nhl(
        {
            "2099-10-01": week("2099-10-01", [game(2099020001)], next_start="2099-10-08"),
            "2099-10-08": week("2099-10-08", [game(2099020002)], next_start="2099-10-15"),
            "2099-10-15": week("2099-10-15", [game(2099020003, "FUT")], next_start="2099-10-22"),
        }
    )
    first = fetch_schedule(db, client, SEASON, TODAY)
    second = fetch_schedule(db, client, SEASON, TODAY)

    assert requested == ["2099-10-01", "2099-10-08", "2099-10-15", "2099-10-15"]
    assert (first.fetched, first.new, first.skipped) == (3, 3, 0)
    assert (second.fetched, second.new, second.skipped) == (1, 0, 2)


@pytest.mark.db
def test_paging_backwards_is_an_error(db):
    client, _ = fake_nhl({"2099-10-01": week("2099-10-01", next_start="2099-09-24")})

    with pytest.raises(RuntimeError, match="backwards"):
        fetch_schedule(db, client, SEASON, TODAY)


@pytest.mark.db
def test_load_writes_games_then_only_changes(db):
    raw = store_week(db, week("2099-10-01", [game(2099020001, home_score=3)]), "2099-10-01")
    load_schedules(db)
    row = db.execute("select * from games where game_id = 2099020001").fetchone()

    assert (row["home_score"], row["away_score"], row["home_win"]) == (3, 2, True)
    assert row["game_date"] == date(2099, 10, 1)
    assert row["source_raw_id"] == raw.id
    assert load_schedules(db).games_changed == 0

    store_week(db, week("2099-10-01", [game(2099020001, home_score=1)]), "2099-10-01")
    assert load_schedules(db).games_changed == 1
    row = db.execute("select home_win from games where game_id = 2099020001").fetchone()
    assert row["home_win"] is False


@pytest.mark.db
def test_team_names_come_from_the_schedule(db):
    store_week(db, week("2099-10-01", [game(2099020001)]), "2099-10-01")
    load_schedules(db)
    names = db.execute("select name from teams where team_id in (901, 902) order by team_id")

    assert [r["name"] for r in names] == ["Place Club901", "Place Club902"]


@pytest.mark.db
def test_game_in_two_weeks_keeps_the_final_version(db):
    store_week(db, week("2099-10-08", [game(2099020005, "OFF")]), "2099-10-08")
    store_week(db, week("2099-10-01", [game(2099020005, "FUT")]), "2099-10-01")
    load_schedules(db)
    row = db.execute("select game_state, game_date from games where game_id = 2099020005")

    assert row.fetchone() == {"game_state": "OFF", "game_date": date(2099, 10, 8)}
