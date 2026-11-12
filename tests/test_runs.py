from contextlib import nullcontext
from uuid import uuid4

import psycopg
import pytest

from nhl_forecast.jobs.runs import finish_run, recorded_run, start_run

pytestmark = pytest.mark.db


def run_row(db, run_id):
    return db.execute("select * from job_runs where run_id = %s", (run_id,)).fetchone()


def test_a_run_is_running_until_finished(db):
    run_id = uuid4()
    start_run(db, run_id, "daily", "abc123")
    assert run_row(db, run_id) | {"started_at": None} == {
        "run_id": run_id,
        "job": "daily",
        "started_at": None,
        "finished_at": None,
        "status": "running",
        "prediction_counts": None,
        "error": None,
        "git_commit": "abc123",
    }

    finish_run(db, run_id, "ok", {"elo-v1": {"written": 2, "unchanged": 1, "already_started": 0}})
    row = run_row(db, run_id)
    assert row["status"] == "ok"
    assert row["finished_at"] is not None
    assert row["prediction_counts"]["elo-v1"]["written"] == 2


def test_status_and_finish_time_must_agree(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute(
                "insert into job_runs (run_id, job, status) values (%s, 'daily', 'ok')", (uuid4(),)
            )


def test_recorded_run_stores_the_status_the_job_set(db):
    with recorded_run("daily", lambda: nullcontext(db)) as run:
        run.status = "validation_failed"
        run.prediction_counts = {"elo-v1": {"written": 1, "unchanged": 0, "already_started": 0}}

    row = run_row(db, run.run_id)
    assert row["status"] == "validation_failed"
    assert row["prediction_counts"]["elo-v1"]["written"] == 1
    assert row["error"] is None


def test_recorded_run_marks_a_crash_as_failed_and_reraises(db):
    with pytest.raises(ValueError, match="boom"):
        with recorded_run("daily", lambda: nullcontext(db)) as run:
            raise ValueError("boom")

    row = run_row(db, run.run_id)
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error"] == "ValueError: boom"
