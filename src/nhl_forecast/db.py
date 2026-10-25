from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from nhl_forecast.config import get_settings


@contextmanager
def connect(database_url: str | None = None) -> Iterator[psycopg.Connection]:
    url = database_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row, prepare_threshold=None) as conn:
        yield conn
