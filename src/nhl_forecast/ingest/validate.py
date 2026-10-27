from dataclasses import dataclass

import psycopg

from nhl_forecast.ingest.schedule import SEASON_ENDPOINT
from nhl_forecast.nhl_api.parse import REGULAR_SEASON, Season, parse_seasons

MAX_LISTED = 10


@dataclass(frozen=True)
class SeasonSummary:
    season: int
    games: int
    expected: int
    finals: int
    overtime: int
    shootouts: int
    home_win_rate: float | None


@dataclass(frozen=True)
class Check:
    name: str
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class ValidationReport:
    seasons: tuple[SeasonSummary, ...]
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def validate_games(conn: psycopg.Connection) -> ValidationReport:
    seasons, counts = _season_counts(conn, _stored_seasons(conn))
    checks = (
        counts,
        _check(
            conn,
            "no team plays twice on one day",
            """
            select team_id || ' on ' || game_date as problem from (
                select home_team_id as team_id, game_date from games where game_type in (2, 3)
                union all
                select away_team_id, game_date from games where game_type in (2, 3)
            ) t
            group by team_id, game_date having count(*) > 1
            order by game_date
            """,
        ),
        _check(
            conn,
            "every final game has scores and a result type",
            """
            select game_id::text as problem from games
            where game_state in ('OFF', 'FINAL')
              and (home_score is null or away_score is null or last_period_type is null)
            order by game_id
            """,
        ),
        _check(
            conn,
            "every OT/SO game was won by exactly 1",
            """
            select game_id || ' (' || away_score || '-' || home_score || ' ' || last_period_type
                || ')' as problem
            from games
            where game_state in ('OFF', 'FINAL') and last_period_type in ('OT', 'SO')
              and abs(home_score - away_score) <> 1
            order by game_id
            """,
        ),
        _check(
            conn,
            "games that started over 12 hours ago are final",
            """
            select game_id || ' (' || game_state || ')' as problem from games
            where start_time_utc < now() - interval '12 hours'
              and game_state not in ('OFF', 'FINAL')
            order by game_id
            """,
        ),
    )
    return ValidationReport(seasons=seasons, checks=checks)


def _stored_seasons(conn: psycopg.Connection) -> dict[int, Season]:
    row = conn.execute(
        "select payload from raw_api_responses where endpoint = %s"
        " order by fetched_at desc, id desc limit 1",
        (SEASON_ENDPOINT,),
    ).fetchone()
    if row is None:
        raise RuntimeError("no stored season list; run fetch-schedule first")
    return {s.season: s for s in parse_seasons(row["payload"])}


def _season_counts(
    conn: psycopg.Connection, known: dict[int, Season]
) -> tuple[tuple[SeasonSummary, ...], Check]:
    rows = conn.execute(
        """
        select season,
               array_agg(game_id %% 10000) as numbers,
               count(*) filter (where home_win is not null) as finals,
               count(*) filter (where last_period_type = 'OT') as overtime,
               count(*) filter (where last_period_type = 'SO') as shootouts,
               avg(home_win::int) as home_win_rate
        from games where game_type = %s
        group by season order by season
        """,
        (REGULAR_SEASON,),
    ).fetchall()

    summaries, problems = [], []
    for r in rows:
        if r["season"] not in known:
            problems.append(f"{r['season']}: not in the season list")
            continue
        expected = known[r["season"]].regular_season_games
        # Regular-season game ids end in a game number from 1 to the season's total.
        missing = sorted(set(range(1, expected + 1)) - set(r["numbers"]))
        extra = sorted(set(r["numbers"]) - set(range(1, expected + 1)))
        if missing or extra:
            problems.append(
                f"{r['season']}: {len(r['numbers'])} games, expected {expected};"
                f" missing {_listed(missing)}; unexpected {_listed(extra)}"
            )
        rate = r["home_win_rate"]
        summaries.append(
            SeasonSummary(
                season=r["season"],
                games=len(r["numbers"]),
                expected=expected,
                finals=r["finals"],
                overtime=r["overtime"],
                shootouts=r["shootouts"],
                home_win_rate=float(rate) if rate is not None else None,
            )
        )
    return tuple(summaries), Check("regular-season games match the season list", tuple(problems))


def _check(conn: psycopg.Connection, name: str, sql: str) -> Check:
    return Check(name, tuple(r["problem"] for r in conn.execute(sql).fetchall()))


def _listed(numbers: list[int]) -> str:
    if not numbers:
        return "none"
    shown = ", ".join(f"#{n}" for n in numbers[:MAX_LISTED])
    return shown + (f" and {len(numbers) - MAX_LISTED} more" if len(numbers) > MAX_LISTED else "")


def format_report(report: ValidationReport) -> str:
    lines = [
        f"{'season':<10}{'games':>7}{'expected':>10}{'final':>7}{'OT':>6}{'SO':>5}{'home win':>10}"
    ]
    for s in report.seasons:
        rate = f"{s.home_win_rate:.3f}" if s.home_win_rate is not None else "-"
        lines.append(
            f"{s.season:<10}{s.games:>7}{s.expected:>10}{s.finals:>7}"
            f"{s.overtime:>6}{s.shootouts:>5}{rate:>10}"
        )
    lines.append("")
    for c in report.checks:
        lines.append(f"{'PASS' if c.ok else 'FAIL'}  {c.name}")
        lines.extend(f"      {p}" for p in c.problems[:MAX_LISTED])
        if len(c.problems) > MAX_LISTED:
            lines.append(f"      ... and {len(c.problems) - MAX_LISTED} more")
    return "\n".join(lines)
