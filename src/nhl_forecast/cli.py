"""Command-line entry point: `uv run nhl-forecast --help`."""

import logging
from datetime import UTC, datetime

import typer

from nhl_forecast.db import connect
from nhl_forecast.ingest.schedule import fetch_schedule as fetch_season_schedule
from nhl_forecast.ingest.schedule import fetch_seasons, load_schedules
from nhl_forecast.ingest.validate import format_report, validate_games
from nhl_forecast.nhl_api.client import NhlClient

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
            last = max(s.season for s in seasons if s.start_date <= today)
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


if __name__ == "__main__":
    app()
