import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from nhl_forecast.db import connect

log = logging.getLogger(__name__)


@dataclass
class Run:
    run_id: UUID
    status: str = "ok"
    prediction_counts: dict | None = None


def start_run(conn: psycopg.Connection, run_id: UUID, job: str, git_commit: str | None) -> None:
    conn.execute(
        "insert into job_runs (run_id, job, git_commit) values (%s, %s, %s)",
        (run_id, job, git_commit),
    )


def finish_run(
    conn: psycopg.Connection,
    run_id: UUID,
    status: str,
    prediction_counts: dict | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        update job_runs
        set status = %s, finished_at = now(), prediction_counts = %s, error = %s
        where run_id = %s
        """,
        (status, None if prediction_counts is None else Jsonb(prediction_counts), error, run_id),
    )


@contextmanager
def recorded_run(
    job: str,
    open_conn: Callable[[], AbstractContextManager[psycopg.Connection]] = lambda: connect(
        autocommit=True
    ),
) -> Iterator[Run]:
    """Log a job run: 'running' on entry, then the run's status, or 'failed' if it raises."""
    run = Run(uuid4())
    with open_conn() as conn:
        start_run(conn, run.run_id, job, current_commit())
    try:
        yield run
    except BaseException as exc:
        try:
            with open_conn() as conn:
                finish_run(
                    conn,
                    run.run_id,
                    "failed",
                    run.prediction_counts,
                    f"{type(exc).__name__}: {exc}",
                )
        except Exception:
            log.exception("could not record failed run %s", run.run_id)
        raise
    with open_conn() as conn:
        finish_run(conn, run.run_id, run.status, run.prediction_counts)


def current_commit() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return head.stdout.strip() or None
