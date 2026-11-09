from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import psycopg
from numpy.typing import NDArray
from psycopg.types.json import Jsonb

from nhl_forecast.eval.backtest import VALIDATION_SEASON, Dataset, load_dataset
from nhl_forecast.eval.metrics import (
    PairedComparison,
    Scores,
    calibration,
    log_losses,
    paired_bootstrap,
    score,
)
from nhl_forecast.models.elo import EloParams, EloRun, run_elo

ELO_V1 = "elo-v1"
ELO_V1_PARAMS = EloParams(k=9.5, hfa=30.0)
TEST_SEASON = 20252026
TRAINING_SEASONS = [s * 10001 + 1 for s in range(2016, 2024)]


class AlreadyTested(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalTest:
    elo: Scores
    constant: Scores
    coin_flip: Scores
    ece: float
    versus_constant: PairedComparison


def freeze_elo(
    conn: psycopg.Connection, git_commit: str, params: EloParams = ELO_V1_PARAMS
) -> dict[str, Any]:
    """Register the model and store its results through the validation season."""
    hyperparams = {
        "k": params.k,
        "hfa": params.hfa,
        "regress": params.regress,
        "use_margin": params.use_margin,
    }
    existing = conn.execute(
        "select hyperparams, backtest_metrics from model_versions where version = %s", (ELO_V1,)
    ).fetchone()
    if existing is not None and existing["hyperparams"] != hyperparams:
        raise ValueError(f"{ELO_V1} is already frozen with {existing['hyperparams']}")

    dataset = load_dataset(conn, VALIDATION_SEASON)
    run = run_elo(dataset.games, params, dataset.lineage)
    p = _probabilities(run)
    train, validation = dataset.train_mask(), dataset.validation_mask()
    metrics = {
        "train": asdict(score(p[train], dataset.outcomes[train])),
        "validation": asdict(score(p[validation], dataset.outcomes[validation])),
        "validation_ece": calibration(p[validation], dataset.outcomes[validation]).ece,
    }

    if existing is None:
        conn.execute(
            """
            insert into model_versions (version, family, description, hyperparams,
                                        training_seasons, git_commit, backtest_metrics, status)
            values (%s, 'elo', %s, %s, %s, %s, %s, 'primary')
            """,
            (
                ELO_V1,
                "Plain Elo: win/loss updates, playoffs included, one shared home-ice value",
                Jsonb(hyperparams),
                TRAINING_SEASONS,
                git_commit,
                Jsonb(metrics),
            ),
        )
    _store_run(conn, dataset, run, train | validation, np.ones(len(p), dtype=bool))
    return metrics


def run_final_test(conn: psycopg.Connection, params: EloParams = ELO_V1_PARAMS) -> FinalTest:
    """Score the test season once. Refuses to run if a test result is already recorded."""
    row = conn.execute(
        "select backtest_metrics from model_versions where version = %s", (ELO_V1,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{ELO_V1} is not frozen; run freeze-elo first")
    if "test" in row["backtest_metrics"]:
        raise AlreadyTested(f"{ELO_V1} was already tested: {row['backtest_metrics']['test']}")

    dataset = load_dataset(conn, TEST_SEASON)
    test = dataset.regular & (dataset.seasons == TEST_SEASON)
    if not test.any():
        raise RuntimeError(f"no final games stored for season {TEST_SEASON}")

    run = run_elo(dataset.games, params, dataset.lineage)
    p = _probabilities(run)
    y = dataset.outcomes[test]
    rate = float(dataset.outcomes[dataset.train_mask()].mean())
    constant = np.full(y.shape, rate)
    result = FinalTest(
        elo=score(p[test], y),
        constant=score(constant, y),
        coin_flip=score(np.full(y.shape, 0.5), y),
        ece=calibration(p[test], y).ece,
        versus_constant=paired_bootstrap(log_losses(p[test], y), log_losses(constant, y)),
    )

    _store_run(conn, dataset, run, test, dataset.seasons == TEST_SEASON)
    conn.execute(
        """
        update model_versions
        set backtest_metrics = backtest_metrics || jsonb_build_object('test', %s::jsonb)
        where version = %s
        """,
        (Jsonb(asdict(result)), ELO_V1),
    )
    return result


def format_final_test(result: FinalTest) -> str:
    c = result.versus_constant
    lines = [f"{'season ' + str(TEST_SEASON):<16}{'log loss':>10}{'Brier':>8}{'accuracy':>10}"]
    for name, s in (
        (ELO_V1, result.elo),
        ("constant", result.constant),
        ("coin flip", result.coin_flip),
    ):
        lines.append(f"{name:<16}{s.log_loss:>10.4f}{s.brier:>8.4f}{s.accuracy:>10.3f}")
    lines.append(f"games: {result.elo.games}   ECE: {result.ece:.3f}")
    lines.append(
        f"{ELO_V1} vs constant: {c.mean_difference:+.4f} ({c.ci_low:+.4f} to {c.ci_high:+.4f})"
    )
    return "\n".join(lines)


def _probabilities(run: EloRun) -> NDArray[np.float64]:
    return np.array([x.p_home_win for x in run.predictions])


def _store_run(
    conn: psycopg.Connection,
    dataset: Dataset,
    run: EloRun,
    scored: NDArray[np.bool_],
    rated: NDArray[np.bool_],
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into backtest_predictions (model_version, game_id, p_home_win)
            values (%s, %s, %s)
            on conflict (model_version, game_id) do update set p_home_win = excluded.p_home_win
            """,
            [
                (ELO_V1, x.game_id, x.p_home_win)
                for x, keep in zip(run.predictions, scored, strict=True)
                if keep
            ],
        )
        ratings = []
        for x, game, keep in zip(run.predictions, dataset.games, rated, strict=True):
            if keep:
                ratings.append((ELO_V1, game.home_team_id, x.home_pre, x.home_post, x.game_id))
                ratings.append((ELO_V1, game.away_team_id, x.away_pre, x.away_post, x.game_id))
        cur.executemany(
            """
            insert into ratings_history
                (model_version, team_id, game_id, as_of, rating_pre, rating_post)
            select v.version, v.team_id, g.game_id, g.start_time_utc, v.pre, v.post
            from (select %s as version, %s::smallint as team_id,
                         %s::double precision as pre, %s::double precision as post) v
            join games g on g.game_id = %s
            on conflict (model_version, team_id, game_id) do update
                set rating_pre = excluded.rating_pre, rating_post = excluded.rating_post
            """,
            ratings,
        )
