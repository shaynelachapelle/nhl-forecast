import uuid

import psycopg
import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def setup(db):
    db.execute(
        "insert into teams (team_id, abbrev, name) values (901, 'AAA', 'A'), (902, 'BBB', 'B')"
    )
    db.execute(
        """
        insert into games (game_id, season, game_type, game_date, start_time_utc,
                           home_team_id, away_team_id, game_state)
        values (1, 20262027, 2, current_date, now() + interval '1 day', 901, 902, 'FUT'),
               (2, 20262027, 2, current_date, now() - interval '1 hour', 902, 901, 'LIVE')
        """
    )
    db.execute("insert into model_versions (version, family) values ('test-v1', 'elo')")
    return db


def predict(db, game_id, p=0.55, created_at=None):
    return db.execute(
        """
        insert into predictions (game_id, model_version, p_home_win, run_id, created_at)
        values (%s, 'test-v1', %s, %s, coalesce(%s, now()))
        returning prediction_id, created_at
        """,
        (game_id, p, uuid.uuid4(), created_at),
    ).fetchone()


def test_prediction_before_puck_drop_is_accepted(setup):
    assert predict(setup, 1)["prediction_id"] is not None


def test_prediction_after_puck_drop_is_rejected(setup):
    with pytest.raises(psycopg.errors.RaiseException, match="before puck drop"):
        with setup.transaction():
            predict(setup, 2)


def test_created_at_cannot_be_backdated(setup):
    row = predict(setup, 1, created_at="2000-01-01T00:00:00Z")
    now = setup.execute("select now() as now").fetchone()["now"]
    assert row["created_at"] == now


@pytest.mark.parametrize(
    "sql",
    [
        "update predictions set p_home_win = 0.9",
        "delete from predictions",
        "truncate predictions",
    ],
)
def test_predictions_are_append_only(setup, sql):
    predict(setup, 1)
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with setup.transaction():
            setup.execute(sql)


@pytest.mark.parametrize("p", [0.0, 1.0])
def test_probability_must_be_strictly_between_0_and_1(setup, p):
    with pytest.raises(psycopg.errors.CheckViolation):
        with setup.transaction():
            predict(setup, 1, p=p)


def test_home_win_is_derived_once_final(setup):
    setup.execute(
        "update games set game_state = 'OFF', home_score = 2, away_score = 3 where game_id = 2"
    )
    row = setup.execute("select home_win from games where game_id = 2").fetchone()
    assert row["home_win"] is False


def test_identical_raw_payload_is_stored_once(db):
    insert = """
        insert into raw_api_responses (source, endpoint, url, http_status, payload, payload_sha256)
        values ('nhl_web', '/v1/schedule/{date}', 'https://x/1', 200, '{}', 'abc')
        on conflict on constraint raw_api_responses_url_payload_key do nothing
    """
    db.execute(insert)
    db.execute(insert)
    count = db.execute("select count(*) as n from raw_api_responses where url = 'https://x/1'")
    assert count.fetchone()["n"] == 1
