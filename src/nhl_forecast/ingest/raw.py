import hashlib
import json
from typing import Any, NamedTuple

import psycopg
from psycopg.types.json import Jsonb

from nhl_forecast.nhl_api.client import ApiResponse


class StoredRaw(NamedTuple):
    id: int
    is_new: bool


def payload_sha256(payload: Any) -> str:
    """Hashes canonical JSON so key order doesn't matter, matching jsonb."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_raw(conn: psycopg.Connection, response: ApiResponse) -> StoredRaw:
    sha = payload_sha256(response.payload)
    # The second select can't see the CTE's insert, so exactly one branch returns a row.
    row = conn.execute(
        """
        with inserted as (
            insert into raw_api_responses
                (source, endpoint, url, params, http_status, payload, payload_sha256)
            values (%(source)s, %(endpoint)s, %(url)s, %(params)s, %(status)s, %(payload)s, %(sha)s)
            on conflict on constraint raw_api_responses_url_payload_key do nothing
            returning id
        )
        select id, true as is_new from inserted
        union all
        select id, false from raw_api_responses where url = %(url)s and payload_sha256 = %(sha)s
        limit 1
        """,
        {
            "source": response.source,
            "endpoint": response.endpoint,
            "url": response.url,
            "params": Jsonb(response.params),
            "status": response.http_status,
            "payload": Jsonb(response.payload),
            "sha": sha,
        },
    ).fetchone()
    assert row is not None
    return StoredRaw(id=row["id"], is_new=row["is_new"])
