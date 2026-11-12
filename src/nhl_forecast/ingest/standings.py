import logging
from collections.abc import Iterable
from datetime import date

import httpx
import psycopg

from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.nhl_api.client import NhlClient
from nhl_forecast.nhl_api.parse import Season, parse_standings

log = logging.getLogger(__name__)

STANDINGS_ENDPOINT = "/v1/standings/{date}"


def standings_date(season: Season, today: date) -> date | None:
    """Divisions are fixed for a season, so one day of standings is enough."""
    if today <= season.start_date:
        return None
    return min(season.regular_season_end_date, today)


def fetch_standings(
    conn: psycopg.Connection, client: NhlClient, seasons: Iterable[Season], today: date
) -> int:
    """Store one standings response per season, skipping seasons already stored.

    A failed request is logged and skipped: predictions don't depend on divisions.
    """
    fetched = 0
    for season in seasons:
        day = standings_date(season, today)
        if day is None or _has_standings(conn, season.season):
            continue
        try:
            response = client.get_json(
                "nhl_web", STANDINGS_ENDPOINT, path={"date": day.isoformat()}
            )
        except httpx.HTTPError as exc:
            log.warning("standings for %d not fetched: %r", season.season, exc)
            continue
        store_raw(conn, response)
        fetched += 1
    log.info("fetched standings for %d seasons", fetched)
    return fetched


def _has_standings(conn: psycopg.Connection, season: int) -> bool:
    row = conn.execute(
        "select exists (select 1 from raw_api_responses where endpoint = %s"
        " and (payload->'standings'->0->>'seasonId')::int = %s) as stored",
        (STANDINGS_ENDPOINT, season),
    ).fetchone()
    return row["stored"]


TEAM_SEASON_UPSERT = """
    insert into team_seasons (team_id, season, conference, division)
    values (%(team_id)s, %(season)s, %(conference)s, %(division)s)
    on conflict (team_id, season) do update
        set conference = excluded.conference, division = excluded.division
        where (team_seasons.conference, team_seasons.division)
            is distinct from (excluded.conference, excluded.division)
"""


def load_team_seasons(conn: psycopg.Connection) -> int:
    """Upsert conference and division per team and season; returns rows changed.

    Standings name teams by abbreviation, which isn't unique over time (UTA is 59 and 68),
    so each one is matched to the team that played regular-season games that season.
    """
    with conn.transaction():
        payloads = conn.execute(
            """
            select distinct on (season) payload from (
                select distinct on (url) params->>'date' as day, payload,
                       (payload->'standings'->0->>'seasonId')::int as season
                from raw_api_responses
                where endpoint = %s
                order by url, fetched_at desc, id desc
            ) latest
            where season is not null
            order by season, day desc
            """,
            (STANDINGS_ENDPOINT,),
        ).fetchall()
        team_ids: dict[tuple[int, str], set[int]] = {}
        for r in conn.execute(
            """
            select distinct g.season, t.abbrev, t.team_id
            from games g join teams t on t.team_id in (g.home_team_id, g.away_team_id)
            where g.game_type = 2
            """
        ):
            team_ids.setdefault((r["season"], r["abbrev"]), set()).add(r["team_id"])

        rows = []
        for p in payloads:
            for s in parse_standings(p["payload"]):
                ids = team_ids.get((s.season, s.abbrev), set())
                if len(ids) != 1:
                    log.warning("%d %s: matches teams %s; skipped", s.season, s.abbrev, sorted(ids))
                    continue
                rows.append(
                    {
                        "team_id": ids.pop(),
                        "season": s.season,
                        "conference": s.conference,
                        "division": s.division,
                    }
                )
        changed = 0
        if rows:
            with conn.cursor() as cur:
                cur.executemany(TEAM_SEASON_UPSERT, rows)
                changed = cur.rowcount
    log.info("team seasons: %d from standings, %d inserted or changed", len(rows), changed)
    return changed
