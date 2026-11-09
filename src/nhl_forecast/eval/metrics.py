from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class Scores:
    games: int
    log_loss: float
    brier: float
    accuracy: float


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    games: int
    mean_predicted: float
    observed: float


@dataclass(frozen=True)
class Calibration:
    bins: tuple[CalibrationBin, ...]
    ece: float


@dataclass(frozen=True)
class PairedComparison:
    mean_difference: float  # negative means A has the lower loss
    ci_low: float
    ci_high: float


def log_losses(p: ArrayLike, y: ArrayLike) -> NDArray[np.float64]:
    p, y = _validated(p, y)
    if np.any((p <= 0) | (p >= 1)):
        raise ValueError("log loss needs probabilities strictly between 0 and 1")
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def log_loss(p: ArrayLike, y: ArrayLike) -> float:
    return float(log_losses(p, y).mean())


def brier(p: ArrayLike, y: ArrayLike) -> float:
    p, y = _validated(p, y)
    return float(((p - y) ** 2).mean())


def accuracy(p: ArrayLike, y: ArrayLike) -> float:
    """A probability of exactly 0.5 counts as picking the home team."""
    p, y = _validated(p, y)
    return float(((p >= 0.5) == (y == 1)).mean())


def score(p: ArrayLike, y: ArrayLike) -> Scores:
    p, y = _validated(p, y)
    return Scores(games=len(p), log_loss=log_loss(p, y), brier=brier(p, y), accuracy=accuracy(p, y))


def calibration(p: ArrayLike, y: ArrayLike, bins: int = 20) -> Calibration:
    p, y = _validated(p, y)
    edges = np.linspace(0, 1, bins + 1)
    index = np.minimum(np.floor(p * bins + 1e-9).astype(int), bins - 1)

    result = []
    ece = 0.0
    for i in np.unique(index):
        in_bin = index == i
        predicted, observed = float(p[in_bin].mean()), float(y[in_bin].mean())
        ece += in_bin.mean() * abs(predicted - observed)
        result.append(
            CalibrationBin(
                lower=float(edges[i]),
                upper=float(edges[i + 1]),
                games=int(in_bin.sum()),
                mean_predicted=predicted,
                observed=observed,
            )
        )
    return Calibration(bins=tuple(result), ece=float(ece))


def paired_bootstrap(
    losses_a: ArrayLike,
    losses_b: ArrayLike,
    *,
    samples: int = 10_000,
    confidence: float = 0.95,
    seed: int = 0,
) -> PairedComparison:
    difference = np.asarray(losses_a, dtype=float) - np.asarray(losses_b, dtype=float)
    if difference.ndim != 1 or difference.size == 0:
        raise ValueError("need two equal-length, non-empty arrays of per-game losses")

    rng = np.random.default_rng(seed)
    n = difference.size
    means = np.array([difference[rng.integers(0, n, n)].mean() for _ in range(samples)])
    tail = (1 - confidence) / 2
    low, high = np.quantile(means, [tail, 1 - tail])
    return PairedComparison(float(difference.mean()), float(low), float(high))


def _validated(p: ArrayLike, y: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if p.ndim != 1 or p.shape != y.shape or p.size == 0:
        raise ValueError("need two equal-length, non-empty 1-D arrays")
    if np.any((p < 0) | (p > 1)):
        raise ValueError("probabilities must be between 0 and 1")
    if np.any((y != 0) & (y != 1)):
        raise ValueError("outcomes must be 0 or 1")
    return p, y
