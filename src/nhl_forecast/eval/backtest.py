from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import psycopg
from numpy.typing import NDArray

from nhl_forecast.eval.metrics import (
    PairedComparison,
    Scores,
    calibration,
    log_loss,
    log_losses,
    paired_bootstrap,
    score,
)
from nhl_forecast.models.elo import EloGame, EloParams, run_elo
from nhl_forecast.nhl_api.parse import REGULAR_SEASON

FIRST_TRAIN_SEASON = 20162017
LAST_TRAIN_SEASON = 20232024
NO_CROWDS_SEASON = 20202021
VALIDATION_SEASON = 20242025

K_GRID = tuple(float(k) for k in np.arange(2, 12.01, 0.5))
HFA_GRID = tuple(float(h) for h in np.arange(20, 50.01, 2.5))
NO_CROWDS_HFA_GRID = tuple(float(h) for h in np.arange(0, 50.01, 5))


@dataclass(frozen=True)
class Dataset:
    games: tuple[EloGame, ...]
    regular: NDArray[np.bool_]
    seasons: NDArray[np.int64]
    outcomes: NDArray[np.float64]
    lineage: dict[int, int]

    def train_mask(self, include_no_crowds: bool = True) -> NDArray[np.bool_]:
        mask = self.regular & (self.seasons >= FIRST_TRAIN_SEASON)
        mask &= self.seasons <= LAST_TRAIN_SEASON
        return mask if include_no_crowds else mask & (self.seasons != NO_CROWDS_SEASON)

    def validation_mask(self) -> NDArray[np.bool_]:
        return self.regular & (self.seasons == VALIDATION_SEASON)


@dataclass(frozen=True)
class Variant:
    name: str
    use_margin: bool = False
    regress: float = 1 / 3
    playoffs: bool = True
    own_no_crowds_hfa: bool = False
    tune_on_no_crowds: bool = True


VARIANTS = (
    Variant("plain"),
    Variant("margin", use_margin=True),
    Variant("no playoffs", playoffs=False),
    Variant("regress 1/4", regress=1 / 4),
    Variant("regress 1/2", regress=1 / 2),
    Variant("2020-21 own HFA", own_no_crowds_hfa=True),
    Variant("tune without 2020-21", tune_on_no_crowds=False),
)


@dataclass(frozen=True)
class Baseline:
    name: str
    train: Scores
    validation: Scores


@dataclass(frozen=True)
class VariantResult:
    variant: Variant
    params: EloParams
    at_grid_edge: bool
    train: Scores
    validation: Scores
    ece: float
    versus_reference: PairedComparison | None


@dataclass(frozen=True)
class BacktestReport:
    baselines: tuple[Baseline, ...]
    results: tuple[VariantResult, ...]


def load_dataset(conn: psycopg.Connection, last_season: int = VALIDATION_SEASON) -> Dataset:
    rows = conn.execute(
        """
        select game_id, season, game_type, home_team_id, away_team_id, neutral_site, home_win,
               abs(home_score - away_score) as margin
        from games
        where game_type in (2, 3) and home_win is not null and season <= %s
        order by start_time_utc, game_id
        """,
        (last_season,),
    ).fetchall()
    lineage = conn.execute("select team_id, predecessor_team_id from rating_lineage").fetchall()
    return build_dataset(rows, {r["team_id"]: r["predecessor_team_id"] for r in lineage})


def build_dataset(rows: Sequence[dict[str, Any]], lineage: dict[int, int]) -> Dataset:
    return Dataset(
        games=tuple(
            EloGame(
                game_id=r["game_id"],
                season=r["season"],
                home_team_id=r["home_team_id"],
                away_team_id=r["away_team_id"],
                neutral_site=r["neutral_site"],
                home_win=r["home_win"],
                margin=r["margin"],
            )
            for r in rows
        ),
        regular=np.array([r["game_type"] == REGULAR_SEASON for r in rows], dtype=bool),
        seasons=np.array([r["season"] for r in rows], dtype=np.int64),
        outcomes=np.array([float(r["home_win"]) for r in rows]),
        lineage=lineage,
    )


def predict(dataset: Dataset, params: EloParams, playoffs: bool = True) -> NDArray[np.float64]:
    """Home-win probability per game; NaN for playoff games left out of the run."""
    if playoffs:
        run = run_elo(dataset.games, params, dataset.lineage)
        return np.array([x.p_home_win for x in run.predictions])
    regular_games = [g for g, keep in zip(dataset.games, dataset.regular, strict=True) if keep]
    run = run_elo(regular_games, params, dataset.lineage)
    p = np.full(len(dataset.games), np.nan)
    p[dataset.regular] = [x.p_home_win for x in run.predictions]
    return p


def tune(
    dataset: Dataset,
    variant: Variant,
    ks: Sequence[float] = K_GRID,
    hfas: Sequence[float] = HFA_GRID,
    no_crowds_hfas: Sequence[float] = NO_CROWDS_HFA_GRID,
) -> tuple[EloParams, bool]:
    """Lowest training log loss for the variant, and whether it sits on the grid's edge."""
    mask = dataset.train_mask(variant.tune_on_no_crowds)
    y = dataset.outcomes[mask]

    def loss(params: EloParams) -> float:
        return log_loss(predict(dataset, params, variant.playoffs)[mask], y)

    candidates = [
        EloParams(k=k, hfa=hfa, regress=variant.regress, use_margin=variant.use_margin)
        for k in ks
        for hfa in hfas
    ]
    best = min(candidates, key=loss)
    at_edge = best.k in (ks[0], ks[-1]) or best.hfa in (hfas[0], hfas[-1])
    if variant.own_no_crowds_hfa:
        best = min(
            (replace(best, hfa_by_season={NO_CROWDS_SEASON: h}) for h in no_crowds_hfas),
            key=loss,
        )
    return best, at_edge


def run_backtest(
    dataset: Dataset,
    variants: Sequence[Variant] = VARIANTS,
    ks: Sequence[float] = K_GRID,
    hfas: Sequence[float] = HFA_GRID,
    no_crowds_hfas: Sequence[float] = NO_CROWDS_HFA_GRID,
) -> BacktestReport:
    """Tune each variant on training seasons and score it on validation against the first."""
    train = dataset.train_mask()
    validation = dataset.validation_mask()
    y_train, y_validation = dataset.outcomes[train], dataset.outcomes[validation]

    results: list[VariantResult] = []
    reference_losses = None
    for variant in variants:
        params, at_edge = tune(dataset, variant, ks, hfas, no_crowds_hfas)
        p = predict(dataset, params, variant.playoffs)
        losses = log_losses(p[validation], y_validation)
        versus = None if reference_losses is None else paired_bootstrap(losses, reference_losses)
        if reference_losses is None:
            reference_losses = losses
        results.append(
            VariantResult(
                variant=variant,
                params=params,
                at_grid_edge=at_edge,
                train=score(p[train], y_train),
                validation=score(p[validation], y_validation),
                ece=calibration(p[validation], y_validation).ece,
                versus_reference=versus,
            )
        )
    return BacktestReport(baselines=_baselines(y_train, y_validation), results=tuple(results))


def _baselines(
    y_train: NDArray[np.float64], y_validation: NDArray[np.float64]
) -> tuple[Baseline, ...]:
    rate = float(y_train.mean())
    return tuple(
        Baseline(
            name,
            score(np.full(y_train.shape, p), y_train),
            score(np.full(y_validation.shape, p), y_validation),
        )
        for name, p in ((f"constant {rate:.4f}", rate), ("coin flip / always home", 0.5))
    )


def format_report(report: BacktestReport) -> str:
    header = (
        f"{'model':<25}{'k':>5}{'hfa':>6}{'train LL':>10}{'val LL':>9}{'Brier':>8}{'acc':>7}"
        f"{'ECE':>7}  val LL vs {report.results[0].variant.name} (95% CI)"
    )
    lines = [header]
    for b in report.baselines:
        lines.append(
            f"{b.name:<25}{'':>5}{'':>6}{b.train.log_loss:>10.4f}{b.validation.log_loss:>9.4f}"
            f"{b.validation.brier:>8.4f}{b.validation.accuracy:>7.3f}"
        )
    for r in report.results:
        c = r.versus_reference
        versus = (
            "reference"
            if c is None
            else f"{c.mean_difference:+.4f} ({c.ci_low:+.4f} to {c.ci_high:+.4f})"
        )
        edge = "  [grid edge]" if r.at_grid_edge else ""
        lines.append(
            f"{r.variant.name:<25}{r.params.k:>5.1f}{r.params.hfa:>6.1f}"
            f"{r.train.log_loss:>10.4f}{r.validation.log_loss:>9.4f}{r.validation.brier:>8.4f}"
            f"{r.validation.accuracy:>7.3f}{r.ece:>7.3f}  {versus}{edge}"
        )
        for season, hfa in r.params.hfa_by_season.items():
            lines.append(f"{'':<25}own HFA for {season}: {hfa:.1f}")
    return "\n".join(lines)
