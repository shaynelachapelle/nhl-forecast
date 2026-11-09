import pytest

from nhl_forecast.eval.freeze import (
    ELO_V1,
    AlreadyTested,
    format_final_test,
    freeze_elo,
    run_final_test,
)
from nhl_forecast.models.elo import EloParams

pytestmark = pytest.mark.db

# season, games: warm-up, training, validation, test
SEASONS = {20152016: 2, 20182019: 3, 20242025: 2, 20252026: 3}


@pytest.fixture
def history(db):
    db.execute(
        "insert into teams (team_id, abbrev, name) values (901, 'AAA', 'A'), (902, 'BBB', 'B')"
    )
    for season, games in SEASONS.items():
        year = season // 10000
        for n in range(1, games + 1):
            insert_game(db, season, year * 1000000 + 20000 + n, f"{year}-10-{n:02d}", 2)
        insert_game(db, season, year * 1000000 + 30001, f"{year + 1}-04-20", 3)
    return db


def insert_game(db, season, game_id, day, game_type):
    db.execute(
        """
        insert into games (game_id, season, game_type, game_date, start_time_utc,
                           home_team_id, away_team_id, game_state, home_score, away_score)
        values (%s, %s, %s, %s, %s, 901, 902, 'OFF', %s, %s)
        """,
        (game_id, season, game_type, day, f"{day}T23:00:00Z", 3 + game_id % 2, 3),
    )


def count(db, sql):
    return db.execute(f"select count(*) as n from {sql}").fetchone()["n"]


def test_freeze_registers_the_model_as_primary(history):
    freeze_elo(history, "abc123")
    row = history.execute("select * from model_versions where version = %s", (ELO_V1,)).fetchone()

    assert row["status"] == "primary"
    assert row["git_commit"] == "abc123"
    assert row["hyperparams"]["k"] == 9.5 and row["hyperparams"]["use_margin"] is False
    assert row["training_seasons"][0] == 20162017 and row["training_seasons"][-1] == 20232024
    assert row["backtest_metrics"]["validation"]["games"] == 2


def test_freeze_stores_scored_predictions_and_ratings_but_nothing_from_the_test_season(history):
    freeze_elo(history, "abc123")

    assert count(history, "backtest_predictions") == 3 + 2
    assert (
        count(
            history,
            "backtest_predictions b join games g using (game_id) "
            "where g.season = 20152016 or g.game_type = 3",
        )
        == 0
    )
    assert count(history, "ratings_history") == 2 * (3 + 4 + 3)
    assert (
        count(history, "ratings_history r join games g using (game_id) where g.season = 20252026")
        == 0
    )


def test_ratings_history_uses_the_game_start_time(history):
    freeze_elo(history, "abc123")
    row = history.execute(
        """
        select r.as_of = g.start_time_utc as matches
        from ratings_history r join games g using (game_id) limit 1
        """
    ).fetchone()

    assert row["matches"] is True


def test_freeze_can_be_rerun_with_the_same_settings(history):
    freeze_elo(history, "abc123")
    predictions = count(history, "backtest_predictions")
    freeze_elo(history, "def456")

    assert count(history, "backtest_predictions") == predictions
    row = history.execute("select git_commit from model_versions").fetchone()
    assert row["git_commit"] == "abc123"


def test_freeze_refuses_different_settings(history):
    freeze_elo(history, "abc123")

    with pytest.raises(ValueError, match="already frozen"):
        freeze_elo(history, "abc123", EloParams(k=6, hfa=30))


def test_final_test_needs_a_frozen_model(history):
    with pytest.raises(RuntimeError, match="not frozen"):
        run_final_test(history)


def test_final_test_scores_only_the_test_season_and_records_it(history):
    freeze_elo(history, "abc123")
    result = run_final_test(history)

    assert result.elo.games == 3
    assert "games: 3" in format_final_test(result)
    metrics = history.execute("select backtest_metrics from model_versions").fetchone()
    assert metrics["backtest_metrics"]["test"]["elo"]["games"] == 3
    assert "validation" in metrics["backtest_metrics"]
    assert (
        count(
            history, "backtest_predictions b join games g using (game_id) where g.season = 20252026"
        )
        == 3
    )
    assert (
        count(history, "ratings_history r join games g using (game_id) where g.season = 20252026")
        == 2 * 4
    )


def test_final_test_runs_only_once(history):
    freeze_elo(history, "abc123")
    run_final_test(history)

    with pytest.raises(AlreadyTested):
        run_final_test(history)


def test_refreezing_keeps_the_recorded_test(history):
    freeze_elo(history, "abc123")
    run_final_test(history)
    freeze_elo(history, "abc123")

    with pytest.raises(AlreadyTested):
        run_final_test(history)
