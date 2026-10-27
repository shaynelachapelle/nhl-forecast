import logging
from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Any

import psycopg

from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.nhl_api.client import NhlClient
from nhl_forecast.nhl_api.parse import (
    FINAL_STATES,
    Game,
    Season,
    Team,
    parse_schedule_week,
    parse_seasons,
)

log = logging.getLogger(__name__)

SCHEDULE_ENDPOINT = "/v1/schedule/{date}"
SEASON_ENDPOINT = "/en/season"


@dataclass
class FetchSummary:
    fetched: int = 0
    new: int = 0
    skipped: int = 0


@dataclass
class LoadSummary:
    weeks: int = 0
    teams_changed: int = 0
    games: int = 0
    games_changed: int = 0


def fetch_seasons(conn: psycopg.Connection, client: NhlClient) -> tuple[Season, ...]:
    response = client.get_json("nhl_stats", SEASON_ENDPOINT)
    store_raw(conn, response)
    return parse_seasons(response.payload)


def week_is_complete(payload: dict[str, Any], today: date) -> bool:
    last_day = date.fromisoformat(payload["gameWeek"][-1]["date"])
    games = parse_schedule_week(payload).games
    return last_day < today and all(g.game_state in FINAL_STATES for g in games)


def fetch_schedule(
    conn: psycopg.Connection, client: NhlClient, season: Season, today: date
) -> FetchSummary:
    summary = FetchSummary()
    week_start: date | None = season.start_date
    while week_start is not None and week_start <= season.end_date:
        path = {"date": week_start.isoformat()}
        stored = _latest_payload(conn, client.url("nhl_web", SCHEDULE_ENDPOINT, path=path))
        if stored is not None and week_is_complete(stored, today):
            summary.skipped += 1
            payload = stored
        else:
            response = client.get_json("nhl_web", SCHEDULE_ENDPOINT, path=path)
            summary.fetched += 1
            summary.new += store_raw(conn, response).is_new
            payload = response.payload

        next_start = payload.get("nextStartDate")
        next_week = date.fromisoformat(next_start) if next_start else None
        if next_week is not None and next_week <= week_start:
            raise RuntimeError(f"schedule paging went backwards: {week_start} -> {next_week}")
        week_start = next_week

    log.info(
        "%d: fetched %d weeks (%d changed), skipped %d complete",
        season.season,
        summary.fetched,
        summary.new,
        summary.skipped,
    )
    return summary


def _latest_payload(conn: psycopg.Connection, url: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select payload from raw_api_responses where url = %s"
        " order by fetched_at desc, id desc limit 1",
        (url,),
    ).fetchone()
    return row["payload"] if row else None


_GAME_COLUMNS = [f.name for f in fields(Game)]
_GAME_COMPARED = [c for c in _GAME_COLUMNS if c != "game_id"]

TEAM_UPSERT = """
    insert into teams (team_id, abbrev, name)
    values (%(team_id)s, %(abbrev)s, %(name)s)
    on conflict (team_id) do update
        set abbrev = excluded.abbrev, name = excluded.name
        where (teams.abbrev, teams.name) is distinct from (excluded.abbrev, excluded.name)
"""

GAME_UPSERT = f"""
    insert into games ({", ".join(_GAME_COLUMNS)}, source_raw_id)
    values ({", ".join(f"%({c})s" for c in _GAME_COLUMNS)}, %(source_raw_id)s)
    on conflict (game_id) do update
        set {", ".join(f"{c} = excluded.{c}" for c in _GAME_COMPARED)},
            source_raw_id = excluded.source_raw_id
        where ({", ".join(f"games.{c}" for c in _GAME_COMPARED)})
            is distinct from ({", ".join(f"excluded.{c}" for c in _GAME_COMPARED)})
"""


def load_schedules(conn: psycopg.Connection) -> LoadSummary:
    summary = LoadSummary()
    teams: dict[int, Team] = {}
    best: dict[int, tuple[tuple, Game, int]] = {}

    with conn.transaction():
        # A named (server-side) cursor streams rows instead of loading every payload at once.
        with conn.cursor(name="schedule_weeks") as cur:
            cur.execute(
                """
                select * from (
                    select distinct on (url) id, fetched_at, params, payload
                    from raw_api_responses
                    where endpoint = %s
                    order by url, fetched_at desc, id desc
                ) latest
                order by params->>'date'
                """,
                (SCHEDULE_ENDPOINT,),
            )
            for row in cur:
                summary.weeks += 1
                week = parse_schedule_week(row["payload"])
                teams.update((t.team_id, t) for t in week.teams)
                for game in week.games:
                    # A game seen in several weeks: prefer a final result, then the newest fetch.
                    rank = (game.game_state in FINAL_STATES, row["fetched_at"], row["id"])
                    if game.game_id not in best or rank > best[game.game_id][0]:
                        best[game.game_id] = (rank, game, row["id"])

        with conn.cursor() as cur:
            cur.executemany(TEAM_UPSERT, [asdict(t) for t in teams.values()])
            summary.teams_changed = cur.rowcount
            cur.executemany(
                GAME_UPSERT,
                [asdict(game) | {"source_raw_id": raw_id} for _, game, raw_id in best.values()],
            )
            summary.games_changed = cur.rowcount
        summary.games = len(best)

    log.info(
        "parsed %d weeks: %d games (%d inserted or changed), %d teams changed",
        summary.weeks,
        summary.games,
        summary.games_changed,
        summary.teams_changed,
    )
    return summary
