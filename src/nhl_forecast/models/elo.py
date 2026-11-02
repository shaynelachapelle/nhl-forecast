import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

INITIAL_RATING = 1500.0


@dataclass(frozen=True)
class EloParams:
    k: float
    hfa: float
    regress: float = 1 / 3
    hfa_by_season: Mapping[int, float] = field(default_factory=dict)
    use_margin: bool = False

    def hfa_for(self, season: int) -> float:
        return self.hfa_by_season.get(season, self.hfa)


@dataclass(frozen=True)
class EloGame:
    game_id: int
    season: int
    home_team_id: int
    away_team_id: int
    neutral_site: bool
    home_win: bool | None  # None until the game is final
    margin: int = 1


@dataclass(frozen=True)
class EloPrediction:
    game_id: int
    p_home_win: float
    home_pre: float
    away_pre: float
    home_post: float
    away_post: float


@dataclass(frozen=True)
class EloRun:
    predictions: tuple[EloPrediction, ...]
    ratings: dict[int, float]


def home_win_probability(home: float, away: float, hfa: float) -> float:
    return 1 / (1 + 10 ** ((away - home - hfa) / 400))


def run_elo(
    games: Iterable[EloGame], params: EloParams, lineage: Mapping[int, int] | None = None
) -> EloRun:
    """Rate games in the order given, which must be by start time."""
    lineage = lineage or {}
    ratings: dict[int, float] = {}
    predictions: list[EloPrediction] = []
    current_season: int | None = None

    for game in games:
        if game.season != current_season:
            if current_season is not None:
                if game.season < current_season:
                    raise ValueError(f"game {game.game_id} goes back to season {game.season}")
                _regress(ratings, params.regress)
            current_season = game.season

        for team in (game.home_team_id, game.away_team_id):
            if team not in ratings:
                _add_team(ratings, team, lineage)

        home_pre = ratings[game.home_team_id]
        away_pre = ratings[game.away_team_id]
        hfa = 0.0 if game.neutral_site else params.hfa_for(game.season)
        p = home_win_probability(home_pre, away_pre, hfa)

        change = 0.0
        if game.home_win is not None:
            weight = math.log(game.margin + 1) if params.use_margin else 1.0
            change = params.k * weight * (game.home_win - p)
        ratings[game.home_team_id] = home_pre + change
        ratings[game.away_team_id] = away_pre - change
        predictions.append(
            EloPrediction(
                game_id=game.game_id,
                p_home_win=p,
                home_pre=home_pre,
                away_pre=away_pre,
                home_post=home_pre + change,
                away_post=away_pre - change,
            )
        )
    return EloRun(predictions=tuple(predictions), ratings=ratings)


def _add_team(ratings: dict[int, float], team: int, lineage: Mapping[int, int]) -> None:
    predecessor = lineage.get(team)
    if predecessor in ratings:
        ratings[team] = ratings.pop(predecessor)
    else:
        ratings[team] = _mean(ratings)


def _regress(ratings: dict[int, float], fraction: float) -> None:
    mean = _mean(ratings)
    for team, rating in ratings.items():
        ratings[team] = rating + fraction * (mean - rating)


def _mean(ratings: Mapping[int, float]) -> float:
    return sum(ratings.values()) / len(ratings) if ratings else INITIAL_RATING
