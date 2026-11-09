import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import httpx
import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from nhl_forecast.eval.freeze import params_from, store_run
from nhl_forecast.eval.metrics import PairedComparison, Scores, log_losses, paired_bootstrap, score
from nhl_forecast.ingest.raw import store_raw
from nhl_forecast.models.elo import EloGame, run_elo
from nhl_forecast.nhl_api.client import NhlClient

log = logging.getLogger(__name__)

PREDICTION_WINDOW = timedelta(hours=36)
START_BUFFER = timedelta(minutes=2)
ODDS_ENDPOINT = "/v1/partner-game/{region}/now"
ODDS_REGIONS = ("CA", "US")


@dataclass
class PredictSummary:
    written: int = 0
    unchanged: int = 0
    already_started: int = 0


@dataclass(frozen=True)
class LiveScore:
    version: str
    status: str
    scores: Scores
    versus_primary: PairedComparison | None


def predict_upcoming(conn: psycopg.Connection, run_id: UUID) -> dict[str, PredictSummary]:
    """Predict games starting in the next 36 hours with every primary and shadow model.

    Also stores each model's ratings for the current season's final games.
    """
    games, start_times, lineage = _load_games(conn)
    now = conn.execute("select now() as now").fetchone()["now"]
    upcoming = [
        i
        for i, (game, start) in enumerate(zip(games, start_times, strict=True))
        if game.home_win is None and now + START_BUFFER < start <= now + PREDICTION_WINDOW
    ]
    final = np.array([g.home_win is not None for g in games])
    seasons = np.array([g.season for g in games])
    current = seasons[final].max() if final.any() else None

    summaries = {}
    for model in conn.execute(
        "select version, hyperparams from model_versions"
        " where family = 'elo' and status in ('primary', 'shadow') order by version"
    ).fetchall():
        version = model["version"]
        run = run_elo(games, params_from(model["hyperparams"]), lineage)
        store_run(
            conn,
            version,
            games,
            run,
            np.zeros(len(games), dtype=bool),
            final & (seasons == current),
        )
        summaries[version] = _write_predictions(
            conn, version, run_id, [run.predictions[i] for i in upcoming]
        )
        log.info("%s: %s", version, summaries[version])
    return summaries


def _write_predictions(conn, version, run_id, predictions) -> PredictSummary:
    summary = PredictSummary()
    latest = {
        r["game_id"]: r["p_home_win"]
        for r in conn.execute(
            """
            select distinct on (game_id) game_id, p_home_win from predictions
            where model_version = %s and game_id = any(%s)
            order by game_id, created_at desc, prediction_id desc
            """,
            (version, [p.game_id for p in predictions]),
        ).fetchall()
    }
    for p in predictions:
        if p.game_id in latest and abs(latest[p.game_id] - p.p_home_win) < 1e-9:
            summary.unchanged += 1
            continue
        try:
            with conn.transaction():
                conn.execute(
                    """
                    insert into predictions (game_id, model_version, p_home_win, features, run_id)
                    values (%s, %s, %s, %s, %s)
                    """,
                    (
                        p.game_id,
                        version,
                        p.p_home_win,
                        Jsonb({"home_rating": p.home_pre, "away_rating": p.away_pre}),
                        run_id,
                    ),
                )
            summary.written += 1
        except psycopg.errors.RaiseException:
            summary.already_started += 1
    return summary


def _load_games(conn: psycopg.Connection) -> tuple[list[EloGame], list, dict[int, int]]:
    rows = conn.execute(
        """
        select game_id, season, home_team_id, away_team_id, neutral_site, home_win,
               coalesce(abs(home_score - away_score), 1) as margin, start_time_utc
        from games
        where game_type in (2, 3)
        order by start_time_utc, game_id
        """
    ).fetchall()
    lineage = {
        r["team_id"]: r["predecessor_team_id"]
        for r in conn.execute("select team_id, predecessor_team_id from rating_lineage")
    }
    games = [
        EloGame(
            r["game_id"],
            r["season"],
            r["home_team_id"],
            r["away_team_id"],
            r["neutral_site"],
            r["home_win"],
            r["margin"],
        )
        for r in rows
    ]
    return games, [r["start_time_utc"] for r in rows], lineage


def score_live(conn: psycopg.Connection) -> tuple[LiveScore, ...]:
    """Score each model's latest pre-game prediction on every final game."""
    rows = conn.execute(
        """
        select distinct on (p.model_version, p.game_id)
               p.model_version, m.status, p.game_id, p.p_home_win, g.home_win::int as y
        from predictions p
        join games g using (game_id)
        join model_versions m on m.version = p.model_version
        where g.home_win is not null and p.created_at < g.start_time_utc
        order by p.model_version, p.game_id, p.created_at desc, p.prediction_id desc
        """
    ).fetchall()
    by_model: dict[str, dict[int, tuple[float, int]]] = {}
    status = {}
    for r in rows:
        by_model.setdefault(r["model_version"], {})[r["game_id"]] = (r["p_home_win"], r["y"])
        status[r["model_version"]] = r["status"]
    primary = next((v for v, s in status.items() if s == "primary"), None)

    results = []
    for version, games in sorted(by_model.items(), key=lambda kv: status[kv[0]] != "primary"):
        p, y = (np.array(column) for column in zip(*games.values(), strict=True))
        versus = None
        if version != primary and primary is not None:
            common = sorted(games.keys() & by_model[primary].keys())
            if common:
                mine = np.array([games[g][0] for g in common])
                base = np.array([by_model[primary][g][0] for g in common])
                outcome = np.array([games[g][1] for g in common])
                versus = paired_bootstrap(log_losses(mine, outcome), log_losses(base, outcome))
        results.append(LiveScore(version, status[version], score(p, y), versus))
    return tuple(results)


def format_live_scores(scores: tuple[LiveScore, ...]) -> str:
    if not scores:
        return "no final games with live predictions yet"
    lines = [f"{'model':<16}{'status':<9}{'games':>6}{'log loss':>10}{'Brier':>8}{'acc':>7}"]
    for s in scores:
        versus = ""
        if s.versus_primary is not None:
            c = s.versus_primary
            versus = f"  vs primary {c.mean_difference:+.4f} ({c.ci_low:+.4f} to {c.ci_high:+.4f})"
        lines.append(
            f"{s.version:<16}{s.status:<9}{s.scores.games:>6}{s.scores.log_loss:>10.4f}"
            f"{s.scores.brier:>8.4f}{s.scores.accuracy:>7.3f}{versus}"
        )
    return "\n".join(lines)


def capture_odds(conn: psycopg.Connection, client: NhlClient) -> list[str]:
    """Store the current betting odds; a failure is logged, never fatal."""
    stored = []
    for region in ODDS_REGIONS:
        try:
            response = client.get_json("nhl_web", ODDS_ENDPOINT, path={"region": region})
        except httpx.HTTPError as exc:
            log.warning("odds for %s not captured: %r", region, exc)
            continue
        store_raw(conn, response)
        stored.append(region)
    return stored
