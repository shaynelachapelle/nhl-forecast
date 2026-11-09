"""Command-line entry point: `uv run nhl-forecast --help`."""

import logging
import subprocess
from datetime import UTC, date, datetime
from uuid import uuid4

import typer

from nhl_forecast.db import connect
from nhl_forecast.eval.backtest import format_report as format_backtest
from nhl_forecast.eval.backtest import load_dataset, run_backtest
from nhl_forecast.eval.freeze import ELO_V1, MODELS, format_final_test, run_final_test
from nhl_forecast.eval.freeze import freeze_elo as freeze_model
from nhl_forecast.ingest.schedule import fetch_schedule as fetch_season_schedule
from nhl_forecast.ingest.schedule import fetch_seasons, load_schedules
from nhl_forecast.ingest.validate import format_report, validate_games
from nhl_forecast.jobs.live import capture_odds, format_live_scores, predict_upcoming, score_live
from nhl_forecast.nhl_api.client import NhlClient
from nhl_forecast.nhl_api.parse import Season

app = typer.Typer(help="NHL game forecasting jobs.", no_args_is_help=True)


@app.callback()
def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@app.command()
def db_check() -> None:
    """Connect to DATABASE_URL and list the tables, to confirm the setup works."""
    with connect() as conn:
        version = conn.execute("show server_version").fetchone()["server_version"]
        tables = conn.execute(
            "select table_name from information_schema.tables"
            " where table_schema = 'public' order by table_name"
        ).fetchall()
    typer.echo(f"Connected to Postgres {version}; {len(tables)} tables in public:")
    for row in tables:
        typer.echo(f"  {row['table_name']}")


@app.command()
def fetch_schedule(
    first: int = typer.Option(20152016, help="First season, e.g. 20152016."),
    last: int | None = typer.Option(None, help="Last season. Default: the current one."),
) -> None:
    """Store schedule weeks for each season, skipping weeks already stored as complete."""
    today = datetime.now(UTC).date()
    with connect(autocommit=True) as conn, NhlClient() as client:
        seasons = fetch_seasons(conn, client)
        if last is None:
            last = _current_season(seasons, today).season
        for season in sorted(seasons, key=lambda s: s.season):
            if first <= season.season <= last:
                fetch_season_schedule(conn, client, season, today)


@app.command()
def parse_schedule() -> None:
    """Rebuild teams and games from the latest stored copy of each schedule week."""
    with connect() as conn:
        summary = load_schedules(conn)
    typer.echo(
        f"{summary.games} games from {summary.weeks} weeks;"
        f" {summary.games_changed} inserted or changed, {summary.teams_changed} teams changed"
    )


@app.command("validate-games")
def validate_games_command() -> None:
    """Check the games table against the season list and basic hockey rules."""
    with connect() as conn:
        report = validate_games(conn)
    typer.echo(format_report(report))
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def backtest_elo() -> None:
    """Tune Elo variants on training seasons and compare them on the validation season."""
    with connect() as conn:
        dataset = load_dataset(conn)
    typer.echo(format_backtest(run_backtest(dataset)))


@app.command()
def freeze_elo(
    version: str = typer.Option(ELO_V1, help=f"One of: {', '.join(MODELS)}."),
) -> None:
    """Register an Elo model and store its predictions and ratings through validation."""
    if version not in MODELS:
        typer.echo(f"Unknown model {version!r}; choose one of: {', '.join(MODELS)}")
        raise typer.Exit(1)
    commit = _clean_git_commit()
    with connect() as conn:
        metrics = freeze_model(conn, commit, MODELS[version])
    typer.echo(f"{version} frozen at {commit}: {metrics}")


@app.command()
def test_elo() -> None:
    """Score elo-v1 on the test season. Runs only once; a repeat is refused."""
    _clean_git_commit()
    with connect() as conn:
        result = run_final_test(conn)
    typer.echo(format_final_test(result))


@app.command()
def daily() -> None:
    """Update data, capture odds, update ratings and predict games in the next 36 hours.

    Predictions are written even if validation fails; the command then exits with status 1.
    """
    today = datetime.now(UTC).date()
    with connect(autocommit=True) as conn, NhlClient() as client:
        season = _current_season(fetch_seasons(conn, client), today)
        fetch_season_schedule(conn, client, season, today)
        capture_odds(conn, client)
    with connect() as conn:
        load_schedules(conn)
    with connect() as conn:
        report = validate_games(conn)
    with connect() as conn:
        summaries = predict_upcoming(conn, uuid4())
    with connect() as conn:
        scores = score_live(conn)

    for version, summary in summaries.items():
        typer.echo(f"{version}: {summary}")
    typer.echo(format_live_scores(scores))
    if not report.ok:
        typer.echo(format_report(report))
        raise typer.Exit(1)


@app.command("score-live")
def score_live_command() -> None:
    """Score live predictions: each model's latest pre-game prediction on final games."""
    with connect() as conn:
        typer.echo(format_live_scores(score_live(conn)))


def _current_season(seasons: tuple[Season, ...], today: date) -> Season:
    return max((s for s in seasons if s.start_date <= today), key=lambda s: s.season)


def _clean_git_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        typer.echo("Commit your changes first, so the result can be traced to exact code.")
        raise typer.Exit(1)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return head.stdout.strip()


if __name__ == "__main__":
    app()
