import math

import numpy as np
import pytest

from nhl_forecast.eval.metrics import (
    accuracy,
    brier,
    calibration,
    log_loss,
    log_losses,
    paired_bootstrap,
    score,
)


def test_log_loss_of_a_coin_flip_is_ln_2():
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))


def test_log_loss_per_game():
    assert log_losses([0.6, 0.6], [1, 0]) == pytest.approx([-math.log(0.6), -math.log(0.4)])


@pytest.mark.parametrize("p", [0.0, 1.0])
def test_log_loss_rejects_certainty(p):
    with pytest.raises(ValueError, match="strictly between"):
        log_loss([p], [1])


def test_brier_by_hand():
    assert brier([0.5], [1]) == pytest.approx(0.25)
    assert brier([0.6, 0.6], [1, 0]) == pytest.approx((0.16 + 0.36) / 2)


def test_accuracy_counts_even_odds_as_a_home_pick():
    assert accuracy([0.6, 0.4, 0.7], [1, 0, 0]) == pytest.approx(2 / 3)
    assert accuracy([0.5, 0.5, 0.5, 0.5], [1, 1, 1, 0]) == pytest.approx(0.75)


def test_score_combines_the_metrics():
    result = score([0.5, 0.5], [1, 0])

    assert result.games == 2
    assert result.log_loss == pytest.approx(math.log(2))
    assert result.brier == pytest.approx(0.25)
    assert result.accuracy == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("p", "y"),
    [([0.5], [1, 0]), ([], []), ([1.2], [1]), ([0.5], [2]), ([[0.5]], [[1]])],
    ids=["length-mismatch", "empty", "probability-above-1", "outcome-not-binary", "not-1d"],
)
def test_bad_input_is_rejected(p, y):
    with pytest.raises(ValueError):
        brier(p, y)


def test_calibration_by_hand():
    result = calibration([0.52, 0.54, 0.58, 0.72], [1, 0, 1, 1], bins=10)

    assert [b.lower for b in result.bins] == pytest.approx([0.5, 0.7])
    assert [b.upper for b in result.bins] == pytest.approx([0.6, 0.8])
    assert [b.games for b in result.bins] == [3, 1]
    assert result.bins[0].mean_predicted == pytest.approx(0.5466667, abs=1e-6)
    assert result.bins[0].observed == pytest.approx(2 / 3)
    assert result.ece == pytest.approx(0.75 * abs(0.5466667 - 2 / 3) + 0.25 * 0.28, abs=1e-6)


def test_calibration_of_a_well_calibrated_forecast_is_zero():
    assert calibration([0.5] * 4, [1, 1, 0, 0]).ece == pytest.approx(0)


def test_calibration_puts_values_on_an_edge_in_the_upper_bin():
    result = calibration([0.3, 0.6, 1.0], [0, 1, 1], bins=10)

    assert [b.lower for b in result.bins] == pytest.approx([0.3, 0.6, 0.9])


def test_paired_bootstrap_with_a_constant_gap_has_no_uncertainty():
    b = np.linspace(0.5, 0.9, 200)
    result = paired_bootstrap(b - 0.01, b, samples=200)

    assert result.mean_difference == pytest.approx(-0.01)
    assert (result.ci_low, result.ci_high) == pytest.approx((-0.01, -0.01))


def test_paired_bootstrap_interval_contains_zero_for_pure_noise():
    rng = np.random.default_rng(1)
    result = paired_bootstrap(rng.normal(0.68, 0.1, 2000), rng.normal(0.68, 0.1, 2000), samples=500)

    assert result.ci_low < 0 < result.ci_high


def test_paired_bootstrap_is_repeatable_for_a_seed():
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=100), rng.normal(size=100)

    assert paired_bootstrap(a, b, samples=100) == paired_bootstrap(a, b, samples=100)


def test_paired_bootstrap_rejects_empty_input():
    with pytest.raises(ValueError):
        paired_bootstrap([], [])
