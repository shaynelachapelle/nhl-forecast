import json
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    """Load a saved real API response from tests/fixtures."""

    def _load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def db():
    """A connection whose changes are rolled back after each test."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    dbname = conninfo_to_dict(url).get("dbname", "")
    if not dbname.endswith("_test"):
        pytest.fail(
            f"TEST_DATABASE_URL points at database {dbname!r}; tests only run against a"
            " database named *_test (create one with scripts/reset_test_db.sh)"
        )
    with psycopg.connect(url, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()
