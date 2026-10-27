from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

REGULAR_SEASON = 2
PLAYOFFS = 3
GAME_TYPES = {REGULAR_SEASON, PLAYOFFS}
FINAL_STATES = {"OFF", "FINAL"}


@dataclass(frozen=True)
class Season:
    season: int
    start_date: date
    regular_season_end_date: date
    end_date: date
    regular_season_games: int


@dataclass(frozen=True)
class Team:
    team_id: int
    abbrev: str
    name: str


@dataclass(frozen=True)
class Game:
    game_id: int
    season: int
    game_type: int
    game_date: date
    start_time_utc: datetime
    home_team_id: int
    away_team_id: int
    neutral_site: bool
    venue_timezone: str | None
    game_state: str
    home_score: int | None
    away_score: int | None
    last_period_type: str | None


@dataclass(frozen=True)
class ScheduleWeek:
    teams: tuple[Team, ...]
    games: tuple[Game, ...]


def parse_schedule_week(payload: dict[str, Any]) -> ScheduleWeek:
    teams: dict[int, Team] = {}
    games: list[Game] = []
    for day in payload["gameWeek"]:
        for g in day["games"]:
            if g["gameType"] not in GAME_TYPES or g.get("gameScheduleState") != "OK":
                continue
            home, away = g["homeTeam"], g["awayTeam"]
            for side in (home, away):
                teams[side["id"]] = _team(side)
            games.append(
                Game(
                    game_id=g["id"],
                    season=g["season"],
                    game_type=g["gameType"],
                    game_date=date.fromisoformat(day["date"]),
                    start_time_utc=datetime.fromisoformat(g["startTimeUTC"]),
                    home_team_id=home["id"],
                    away_team_id=away["id"],
                    neutral_site=g["neutralSite"],
                    venue_timezone=g.get("venueTimezone"),
                    game_state=g["gameState"],
                    home_score=home.get("score"),
                    away_score=away.get("score"),
                    last_period_type=(g.get("gameOutcome") or {}).get("lastPeriodType"),
                )
            )
    return ScheduleWeek(teams=tuple(teams.values()), games=tuple(games))


def parse_seasons(payload: dict[str, Any]) -> tuple[Season, ...]:
    return tuple(
        Season(
            season=s["id"],
            start_date=_date(s["startDate"]),
            regular_season_end_date=_date(s["regularSeasonEndDate"]),
            end_date=_date(s["endDate"]),
            regular_season_games=s["totalRegularSeasonGames"],
        )
        for s in payload["data"]
    )


def _date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _team(side: dict[str, Any]) -> Team:
    place = side["placeName"]["default"]
    common = side["commonName"]["default"]
    # Utah Hockey Club's commonName already includes the place.
    name = common if common.startswith(place) else f"{place} {common}"
    return Team(team_id=side["id"], abbrev=side["abbrev"], name=name)
