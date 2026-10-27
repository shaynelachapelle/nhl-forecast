import pytest

from nhl_forecast.ingest.raw import payload_sha256, store_raw
from nhl_forecast.nhl_api.client import ApiResponse


def response(payload, url="https://example.test/v1/schedule/2025-10-11"):
    return ApiResponse(
        source="nhl_web",
        endpoint="/v1/schedule/{date}",
        url=url,
        params={"date": "2025-10-11"},
        http_status=200,
        payload=payload,
    )


def test_hash_ignores_key_order():
    assert payload_sha256({"a": 1, "b": [1, 2]}) == payload_sha256({"b": [1, 2], "a": 1})
    assert payload_sha256({"a": 1}) != payload_sha256({"a": 2})


@pytest.mark.db
def test_identical_response_is_stored_once(db):
    first = store_raw(db, response({"games": [1]}))
    again = store_raw(db, response({"games": [1]}))

    assert first.is_new and not again.is_new
    assert again.id == first.id


@pytest.mark.db
def test_changed_content_is_kept_as_a_new_version(db):
    before = store_raw(db, response({"gameState": "FUT"}))
    after = store_raw(db, response({"gameState": "OFF"}))

    assert after.is_new and after.id != before.id
    count = db.execute(
        "select count(*) as n from raw_api_responses where url = %s", (response({}).url,)
    ).fetchone()
    assert count["n"] == 2


@pytest.mark.db
def test_hash_survives_a_round_trip_through_jsonb(db, load_fixture):
    stored = store_raw(db, response(load_fixture("web_schedule_2025-10-11.json")))
    row = db.execute(
        "select payload, payload_sha256 from raw_api_responses where id = %s", (stored.id,)
    ).fetchone()

    assert payload_sha256(row["payload"]) == row["payload_sha256"]
