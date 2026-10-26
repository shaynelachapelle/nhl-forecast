"""Facts about the undocumented NHL APIs that the rest of the code relies on.

If the NHL changes a response, these fail first and say exactly what moved.
"""


def test_schedule_week_has_paging_and_season_bounds(load_fixture):
    week = load_fixture("web_schedule_2025-10-11.json")
    assert {"gameWeek", "nextStartDate", "regularSeasonStartDate", "regularSeasonEndDate"} <= set(
        week
    )
    assert len(week["gameWeek"]) == 7
    assert week["nextStartDate"] == "2025-10-18"


def test_schedule_game_fields(load_fixture):
    game = load_fixture("web_schedule_2025-10-11.json")["gameWeek"][0]["games"][0]
    assert {
        "id",
        "season",
        "gameType",
        "startTimeUTC",
        "neutralSite",
        "venueTimezone",
        "gameState",
        "gameOutcome",
        "homeTeam",
        "awayTeam",
    } <= set(game)
    assert {"id", "abbrev", "score"} <= set(game["homeTeam"])
    assert game["gameOutcome"]["lastPeriodType"] in {"REG", "OT", "SO"}


def test_utah_has_two_team_ids_and_a_new_franchise(load_fixture):
    """Why rating_lineage exists: neither abbrev nor franchise id links UTA to ARI."""
    teams = {t["id"]: t for t in load_fixture("stats_team.json")["data"]}
    assert teams[53]["triCode"] == "ARI"
    assert teams[59]["triCode"] == teams[68]["triCode"] == "UTA"
    assert teams[59]["franchiseId"] == teams[68]["franchiseId"] != teams[53]["franchiseId"]
