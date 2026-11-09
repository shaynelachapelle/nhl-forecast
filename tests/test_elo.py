import math
import random

import pytest

from nhl_forecast.models.elo import EloGame, EloParams, home_win_probability, run_elo

S1, S2, S3 = 20152016, 20162017, 20172018
A, B, C = 1, 2, 3
NO_HFA = EloParams(k=6, hfa=0)


def game(game_id, home=A, away=B, season=S1, win=True, neutral=False):
    return EloGame(game_id, season, home, away, neutral, win)


def test_probability_by_hand():
    assert home_win_probability(1500, 1500, 0) == pytest.approx(0.5)
    assert home_win_probability(1900, 1500, 0) == pytest.approx(10 / 11)
    assert home_win_probability(1500, 1500, 35) == pytest.approx(0.5502, abs=1e-4)
    assert home_win_probability(1500, 1900, 0) == pytest.approx(1 / 11)


def test_home_win_moves_ratings_by_k_times_the_surprise():
    p = run_elo([game(1)], NO_HFA).predictions[0]

    assert (p.home_pre, p.away_pre) == (1500, 1500)
    assert p.p_home_win == pytest.approx(0.5)
    assert (p.home_post, p.away_post) == pytest.approx((1503, 1497))


def test_home_ice_makes_a_home_win_less_surprising():
    p = run_elo([game(1)], EloParams(k=6, hfa=35)).predictions[0]

    assert p.p_home_win == pytest.approx(0.5502, abs=1e-4)
    assert p.home_post - p.home_pre == pytest.approx(6 * (1 - 0.5502), abs=1e-3)


def test_home_loss_lowers_the_home_rating():
    p = run_elo([game(1, win=False)], NO_HFA).predictions[0]

    assert (p.home_post, p.away_post) == pytest.approx((1497, 1503))


def test_next_game_starts_from_the_updated_ratings():
    first, second = run_elo([game(1), game(2, home=B, away=A)], NO_HFA).predictions

    assert (second.home_pre, second.away_pre) == (first.away_post, first.home_post)
    assert second.p_home_win < 0.5


def test_an_upset_moves_ratings_more_than_an_expected_win():
    history = [game(i) for i in range(1, 21)]
    favourite_wins = run_elo([*history, game(99)], NO_HFA).predictions[-1]
    upset = run_elo([*history, game(99, win=False)], NO_HFA).predictions[-1]

    assert abs(upset.home_post - upset.home_pre) > abs(
        favourite_wins.home_post - favourite_wins.home_pre
    )


def test_neutral_site_ignores_home_ice():
    p = run_elo([game(1, neutral=True)], EloParams(k=6, hfa=35)).predictions[0]

    assert p.p_home_win == pytest.approx(0.5)


def test_season_specific_home_ice():
    params = EloParams(k=6, hfa=35, hfa_by_season={S2: 0})
    first, second = run_elo([game(1), game(2, season=S2)], params).predictions

    assert first.p_home_win == pytest.approx(0.5502, abs=1e-4)
    assert second.p_home_win == pytest.approx(
        home_win_probability(second.home_pre, second.away_pre, 0)
    )


def test_ratings_regress_a_third_of_the_way_to_the_mean_between_seasons():
    predictions = run_elo([game(1), game(2, season=S2)], NO_HFA).predictions

    assert predictions[0].home_post == pytest.approx(1503)
    assert (predictions[1].home_pre, predictions[1].away_pre) == pytest.approx((1502, 1498))


def test_regression_happens_once_per_season_change():
    predictions = run_elo([game(1), game(2, season=S2), game(3, season=S2)], NO_HFA).predictions

    assert predictions[2].home_pre == pytest.approx(predictions[1].home_post)


def test_expansion_team_starts_at_the_league_mean():
    games = [game(1), game(2), game(3, home=C, away=A, season=S2)]
    expansion = run_elo(games, NO_HFA).predictions[-1]

    assert expansion.home_pre == pytest.approx(1500)


def test_lineage_team_inherits_the_regressed_rating_and_replaces_its_predecessor():
    games = [
        game(1, home=53, away=A),
        game(2, home=59, away=A, season=S2),
        game(3, home=68, away=A, season=S3),
    ]
    run = run_elo(games, NO_HFA, {59: 53, 68: 59})
    _, second, third = run.predictions

    assert second.home_pre == pytest.approx(1502)
    assert third.home_pre == pytest.approx(1500 + (second.home_post - 1500) * 2 / 3)
    assert set(run.ratings) == {68, A}


def test_league_mean_stays_at_the_starting_rating():
    rng = random.Random(0)
    games = []
    for game_id in range(1, 301):
        season = S1 if game_id <= 100 else S2 if game_id <= 200 else S3
        pool = list(range(1, 8)) + [100 if season == S3 else 8]
        if season >= S2:
            pool.append(9)
        home, away = rng.sample(pool, 2)
        games.append(game(game_id, home, away, season, rng.random() < 0.55))

    ratings = run_elo(games, EloParams(k=6, hfa=35), {100: 8}).ratings

    assert sum(ratings.values()) / len(ratings) == pytest.approx(1500)
    assert 8 not in ratings and 100 in ratings


def test_unplayed_game_is_predicted_without_changing_ratings():
    first, second = run_elo([game(1), game(2, win=None)], NO_HFA).predictions

    assert second.p_home_win > 0.5
    assert (second.home_post, second.away_post) == (second.home_pre, second.away_pre)
    assert second.home_pre == first.home_post


def test_results_keep_the_input_order():
    predictions = run_elo([game(5), game(3), game(9)], NO_HFA).predictions

    assert [p.game_id for p in predictions] == [5, 3, 9]


def test_a_season_going_backwards_is_an_error():
    with pytest.raises(ValueError, match="back to season"):
        run_elo([game(1, season=S2), game(2, season=S1)], NO_HFA)


def test_a_prediction_does_not_depend_on_its_own_result():
    history = [game(1), game(2, home=B, away=A, win=False)]
    after_win = run_elo([*history, game(3, win=True)], NO_HFA).predictions[-1]
    after_loss = run_elo([*history, game(3, win=False)], NO_HFA).predictions[-1]

    assert after_win.p_home_win == after_loss.p_home_win


def test_margin_scales_the_update_by_log_of_margin_plus_one():
    params = EloParams(k=6, hfa=0, use_margin=True)
    one_goal = run_elo([EloGame(1, S1, A, B, False, True, 1)], params).predictions[0]
    three_goals = run_elo([EloGame(1, S1, A, B, False, True, 3)], params).predictions[0]

    assert one_goal.home_post == pytest.approx(1500 + 6 * math.log(2) * 0.5)
    assert three_goals.home_post == pytest.approx(1500 + 6 * math.log(4) * 0.5)
    assert three_goals.away_post == pytest.approx(1500 - 6 * math.log(4) * 0.5)


def test_margin_is_ignored_unless_enabled():
    p = run_elo([EloGame(1, S1, A, B, False, True, 5)], NO_HFA).predictions[0]

    assert p.home_post == pytest.approx(1503)


def test_margin_does_not_change_the_prediction_for_that_game():
    params = EloParams(k=6, hfa=35, use_margin=True)
    p = run_elo([EloGame(1, S1, A, B, False, True, 5)], params).predictions[0]

    assert p.p_home_win == pytest.approx(home_win_probability(1500, 1500, 35))
