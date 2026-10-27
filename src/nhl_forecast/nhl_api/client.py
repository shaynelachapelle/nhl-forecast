import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from nhl_forecast.config import get_settings

log = logging.getLogger(__name__)

Source = Literal["nhl_web", "nhl_stats"]

MIN_INTERVAL_SECONDS = 0.5
MAX_ATTEMPTS = 5
MAX_RETRY_AFTER_SECONDS = 120.0


@dataclass(frozen=True)
class ApiResponse:
    source: Source
    endpoint: str  # path template, e.g. '/v1/schedule/{date}'
    url: str
    params: dict[str, Any]
    http_status: int
    payload: Any


class RateLimiter:
    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            delay = self._last + self.min_interval - now
            if delay > 0:
                self._sleep(delay)
                now += delay
        self._last = now


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class NhlClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        settings = get_settings()
        self._bases: dict[Source, str] = {
            "nhl_web": settings.nhl_web_base,
            "nhl_stats": settings.nhl_stats_base,
        }
        self._http = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )
        self._limiter = RateLimiter(min_interval, clock=clock, sleep=sleep)
        self._backoff = wait_exponential_jitter(initial=1, max=30)
        self._retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(max_attempts),
            wait=self._wait,
            sleep=sleep,
            before_sleep=self._log_retry,
            reraise=True,
        )

    def __enter__(self) -> "NhlClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(
        self,
        source: Source,
        endpoint: str,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> ApiResponse:
        path = dict(path or {})
        query = dict(query or {})
        url = httpx.URL(self._bases[source] + endpoint.format(**path), params=query or None)
        response = self._retrying(self._get_once, url)
        return ApiResponse(
            source=source,
            endpoint=endpoint,
            url=str(url),
            params={**path, **query},
            http_status=response.status_code,
            payload=response.json(),
        )

    def _get_once(self, url: httpx.URL) -> httpx.Response:
        self._limiter.wait()
        response = self._http.get(url)
        response.raise_for_status()
        return response

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, httpx.HTTPStatusError):
            seconds = _retry_after_seconds(exc.response)
            if seconds is not None:
                return min(seconds, MAX_RETRY_AFTER_SECONDS)
        return self._backoff(state)

    @staticmethod
    def _log_retry(state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        wait = state.next_action.sleep if state.next_action else 0.0
        log.warning("attempt %d failed (%r); retrying in %.1fs", state.attempt_number, exc, wait)
