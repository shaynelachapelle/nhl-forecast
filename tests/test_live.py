import math
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from nhl_forecast.eval.freeze import MODELS, hyperparams
from nhl_forecast.jobs.live import capture_odds, format_live_scores, predict_upcoming, score_live
from nhl_forecast.nhl_api.client import NhlClient

pytestmark = pytest.mark.db

FINAL, STARTED, SOON, LATER = 2026020001, 2026020002, 2026020003, 2026020004


@pytest.fixture
def live(db):
    db.execute(
        "insert into teams (team_id, abbrev, name) values (901, 'AAA', 'A'), (902, 'BBB', 'B')"
    )
    for version, status in (("elo-v1", "primary"), ("elo-margin-v1", "shadow"), ("old", "retired")):
        spec = MODELS.get(version, MODELS["elo-v1"])
        db.execute(
            "insert into model_versions (version, family, hyperparams, status)"
            " values (%s, 'elo', %s, %s)",
            (version, Jsonb(hyperparams(spec.params)), status),
        )
    insert_game(db, FINAL, "-1 day", "OFF", (3, 1))
    insert_game(db, STARTED, "-1 hour", "LIVE")
    insert_game(db, SOON, "3 hours", "FUT")
    insert_game(db, LATER, "3 days", "FUT")
    return db


def insert_game(db, game_id, offset, state, score=(None, None)):
    db.execute(
        """
        insert into games (game_id, season, game_type, game_date, start_time_utc,
                           home_team_id, away_team_id, game_state, home_score, away_score,
                           last_period_type)
        values (%s, 20262027, 2, (now() + %s::interval)::date, now() + %s::interval,
                901, 902, %s, %s, %s, %s)
        """,
        (game_id, offset, offset, state, *score, "REG" if state == "OFF" else None),
    )


def predictions(db):
    return db.execute(
        "select model_version, game_id, p_home_win, features, run_id from predictions"
        " order by model_version, prediction_id"
    ).fetchall()


def test_predicts_only_games_starting_in_the_next_36_hours_with_live_models(live):
    summaries = predict_upcoming(live, uuid4())

    assert set(summaries) == {"elo-margin-v1", "elo-v1"}
    assert all(s.written == 1 for s in summaries.values())
    assert [(r["model_version"], r["game_id"]) for r in predictions(live)] == [
        ("elo-margin-v1", SOON),
        ("elo-v1", SOON),
    ]


def test_prediction_records_the_ratings_used_and_the_run(live):
    run_id = uuid4()
    predict_upcoming(live, run_id)
    row = predictions(live)[0]

    assert row["run_id"] == run_id
    assert row["features"]["home_rating"] > row["features"]["away_rating"]
    assert row["p_home_win"] > 0.5


def test_rerun_without_new_results_writes_nothing(live):
    predict_upcoming(live, uuid4())
    again = predict_upcoming(live, uuid4())

    assert all((s.written, s.unchanged) == (0, 1) for s in again.values())
    assert len(predictions(live)) == 2


def test_a_new_result_leads_to_an_updated_prediction(live):
    predict_upcoming(live, uuid4())
    insert_game(live, 2026020005, "-12 hours", "OFF", (0, 4))
    again = predict_upcoming(live, uuid4())

    assert all(s.written == 1 for s in again.values())
    first, second = (r["p_home_win"] for r in predictions(live) if r["model_version"] == "elo-v1")
    assert second < first


def test_ratings_are_stored_for_the_current_seasons_final_games(live):
    predict_upcoming(live, uuid4())
    rows = live.execute(
        "select model_version, game_id from ratings_history order by model_version, team_id"
    ).fetchall()

    assert [(r["model_version"], r["game_id"]) for r in rows] == [
        ("elo-margin-v1", FINAL),
        ("elo-margin-v1", FINAL),
        ("elo-v1", FINAL),
        ("elo-v1", FINAL),
    ]


def test_scoring_uses_each_models_latest_pre_game_prediction(live):
    predict_upcoming(live, uuid4())
    live.execute(
        "insert into predictions (game_id, model_version, p_home_win, run_id)"
        " values (%s, 'elo-v1', 0.7, %s)",
        (SOON, uuid4()),
    )
    live.execute(
        "update games set game_state = 'OFF', home_score = 2, away_score = 1,"
        " last_period_type = 'REG' where game_id = %s",
        (SOON,),
    )
    scores = {s.version: s for s in score_live(live)}

    assert scores["elo-v1"].scores.log_loss == pytest.approx(-math.log(0.7))
    assert scores["elo-v1"].versus_primary is None
    assert scores["elo-margin-v1"].status == "shadow"
    assert scores["elo-margin-v1"].versus_primary is not None
    assert "vs primary" in format_live_scores(tuple(scores.values()))


def test_scoring_with_no_final_games(live):
    predict_upcoming(live, uuid4())

    assert score_live(live) == ()
    assert format_live_scores(()) == "no final games with live predictions yet"


def test_odds_are_stored_and_a_failed_region_is_skipped(db):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/CA/" in request.url.path:
            return httpx.Response(200, json={"games": [], "lastUpdatedUTC": "2026-11-08T12:00:00Z"})
        return httpx.Response(404)

    client = NhlClient(transport=httpx.MockTransport(handler), min_interval=0)

    assert capture_odds(db, client) == ["CA"]
    row = db.execute(
        "select endpoint, params from raw_api_responses where url like '%%partner-game%%'"
    ).fetchone()
    assert row == {"endpoint": "/v1/partner-game/{region}/now", "params": {"region": "CA"}}
