import httpx
import pytest

from nhl_forecast.nhl_api.client import NhlClient, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(responses, clock=None, **kwargs):
    """Each item in responses is an httpx.Response to return, or an exception to raise."""
    clock = clock or FakeClock()
    queue = list(responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = NhlClient(
        transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep, **kwargs
    )
    return client, requests


def test_builds_url_from_template_and_returns_what_raw_storage_needs():
    client, requests = make_client([httpx.Response(200, json={"gameWeek": []})])
    resp = client.get_json("nhl_web", "/v1/schedule/{date}", path={"date": "2025-10-11"})

    assert resp.url == "https://api-web.nhle.com/v1/schedule/2025-10-11"
    assert resp.endpoint == "/v1/schedule/{date}"
    assert resp.params == {"date": "2025-10-11"}
    assert resp.http_status == 200
    assert resp.payload == {"gameWeek": []}
    assert requests[0].headers["User-Agent"].startswith("nhl-forecast")


def test_query_parameters_are_part_of_the_stored_url():
    client, _ = make_client([httpx.Response(200, json={"data": []})])
    resp = client.get_json("nhl_stats", "/en/season", query={"limit": "2"})

    assert resp.url == "https://api.nhle.com/stats/rest/en/season?limit=2"
    assert resp.params == {"limit": "2"}


def test_retry_after_header_sets_the_wait():
    clock = FakeClock()
    client, requests = make_client(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})],
        clock=clock,
    )
    client.get_json("nhl_web", "/v1/x")

    assert len(requests) == 2
    assert clock.sleeps == [7.0]  # no extra rate-limit sleep on top


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(500), httpx.Response(503), httpx.ConnectError("refused")],
    ids=["500", "503", "connect-error"],
)
def test_server_and_network_errors_are_retried(failure):
    client, requests = make_client([failure, failure, httpx.Response(200, json={"ok": True})])

    assert client.get_json("nhl_web", "/v1/x").payload == {"ok": True}
    assert len(requests) == 3


def test_client_errors_are_not_retried():
    client, requests = make_client([httpx.Response(404)])

    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("nhl_web", "/v1/missing")
    assert len(requests) == 1


def test_gives_up_after_max_attempts_with_the_original_error():
    client, requests = make_client([httpx.Response(503)] * 3, max_attempts=3)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        client.get_json("nhl_web", "/v1/x")
    assert excinfo.value.response.status_code == 503
    assert len(requests) == 3


def test_back_to_back_requests_are_spaced_out():
    clock = FakeClock()
    client, _ = make_client(
        [httpx.Response(200, json={}), httpx.Response(200, json={})],
        clock=clock,
        min_interval=0.5,
    )
    client.get_json("nhl_web", "/v1/a")
    client.get_json("nhl_web", "/v1/b")

    assert clock.sleeps == [0.5]


def test_rate_limiter_only_waits_for_the_remaining_time():
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 0.25
    limiter.wait()

    assert clock.sleeps == [0.75]
