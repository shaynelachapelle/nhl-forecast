import numpy as np
import pytest

from nhl_forecast.eval.backtest import (
    VALIDATION_SEASON,
    Variant,
    build_dataset,
    format_report,
    load_dataset,
    predict,
    run_backtest,
    tune,
)
from nhl_forecast.eval.metrics import log_loss
from nhl_forecast.models.elo import EloParams

TRAIN = 20182019
NO_CROWDS = 20202021
A, B, C = 1, 2, 3


def row(game_id, season=TRAIN, game_type=2, home=A, away=B, win=True, margin=1, neutral=False):
    return {
        "game_id": game_id,
        "season": season,
        "game_type": game_type,
        "home_team_id": home,
        "away_team_id": away,
        "neutral_site": neutral,
        "home_win": win,
        "margin": margin,
    }


def sample_rows():
    rng = np.random.default_rng(0)
    rows = []
    for i, season in enumerate([20152016, TRAIN, NO_CROWDS, VALIDATION_SEASON]):
        for n in range(60):
            home, away = rng.choice([A, B, C], 2, replace=False)
            rows.append(
                row(
                    season * 1000 + n,
                    season,
                    home=int(home),
                    away=int(away),
                    win=bool(rng.random() < 0.6 if home == A else rng.random() < 0.5),
                    margin=int(rng.integers(1, 5)),
                )
            )
        rows.append(row(season * 1000 + 99, season, game_type=3, win=i % 2 == 0))
    return rows


def test_masks_score_only_the_right_regular_season_games():
    dataset = build_dataset(sample_rows(), {})

    assert set(dataset.seasons[dataset.train_mask()]) == {TRAIN, NO_CROWDS}
    assert set(dataset.seasons[dataset.train_mask(False)]) == {TRAIN}
    assert set(dataset.seasons[dataset.validation_mask()]) == {VALIDATION_SEASON}
    assert dataset.regular[dataset.train_mask()].all()
    assert dataset.regular[dataset.validation_mask()].all()


def test_leaving_out_playoffs_changes_later_predictions_and_never_scores_them():
    rows = [row(1), row(2, game_type=3), row(3)]
    dataset = build_dataset(rows, {})
    params = EloParams(k=6, hfa=0)
    with_playoffs = predict(dataset, params)
    without = predict(dataset, params, playoffs=False)

    assert np.isnan(without[1])
    assert with_playoffs[2] != without[2]
    assert without[0] == with_playoffs[0]


def test_tune_picks_the_lowest_training_log_loss():
    dataset = build_dataset(sample_rows(), {})
    mask = dataset.train_mask()
    ks, hfas = (2.0, 6.0, 12.0), (0.0, 30.0, 60.0)
    params, _ = tune(dataset, Variant("plain"), ks, hfas)

    losses = {
        (k, h): log_loss(predict(dataset, EloParams(k=k, hfa=h))[mask], dataset.outcomes[mask])
        for k in ks
        for h in hfas
    }
    assert (params.k, params.hfa) == min(losses, key=losses.get)


def test_tune_reports_when_the_best_value_is_on_the_grid_edge():
    dataset = build_dataset(sample_rows(), {})

    assert tune(dataset, Variant("plain"), (6.0,), (30.0,))[1] is True


def test_own_no_crowds_home_ice_is_applied_only_to_that_season():
    dataset = build_dataset(sample_rows(), {})
    params, _ = tune(
        dataset, Variant("own", own_no_crowds_hfa=True), (6.0,), (30.0,), (0.0, 10.0, 30.0)
    )

    assert set(params.hfa_by_season) == {NO_CROWDS}
    assert params.hfa == 30.0


def test_margin_variant_passes_the_setting_through():
    dataset = build_dataset(sample_rows(), {})
    params, _ = tune(dataset, Variant("margin", use_margin=True), (6.0,), (30.0,))

    assert params.use_margin is True
    assert not np.array_equal(predict(dataset, params), predict(dataset, EloParams(k=6, hfa=30)))


def test_report_compares_variants_with_the_first_and_includes_baselines():
    dataset = build_dataset(sample_rows(), {})
    report = run_backtest(
        dataset, [Variant("plain"), Variant("margin", use_margin=True)], (4.0, 6.0), (20.0, 30.0)
    )

    assert report.results[0].versus_reference is None
    assert report.results[1].versus_reference is not None
    assert report.results[0].validation.games == 60
    assert report.baselines[1].validation.log_loss == pytest.approx(np.log(2))
    assert "vs plain" in format_report(report)


@pytest.mark.db
def test_load_dataset_stops_at_the_last_season_and_skips_unfinished_games(db):
    db.execute(
        "insert into teams (team_id, abbrev, name) values (901, 'AAA', 'A'), (902, 'BBB', 'B')"
    )
    rows = [
        (2024020001, 20242025, "OFF", 3, 1),
        (2024020002, 20242025, "FUT", None, None),
        (2025020001, 20252026, "OFF", 4, 2),
    ]
    for game_id, season, state, home_score, away_score in rows:
        db.execute(
            """
            insert into games (game_id, season, game_type, game_date, start_time_utc,
                               home_team_id, away_team_id, game_state, home_score, away_score)
            values (%s, %s, 2, '2024-10-10', '2024-10-10T23:00:00Z', 901, 902, %s, %s, %s)
            """,
            (game_id, season, state, home_score, away_score),
        )

    dataset = load_dataset(db)

    assert [g.game_id for g in dataset.games] == [2024020001]
    assert dataset.games[0].margin == 2
    assert dataset.lineage == {59: 53, 68: 59}
