"""Command-line entry point: `uv run nhl-forecast --help`."""

import typer

from nhl_forecast.db import connect

app = typer.Typer(help="NHL game forecasting jobs.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode while there is only one command."""


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


if __name__ == "__main__":
    app()
