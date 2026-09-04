"""SQLite cache for reusable Agent computation results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import connect, initialize_database


class SQLiteAgentCacheRepository:
    """Small JSON cache backed by SQLite."""

    def __init__(self, database_path: Path | None = None):
        """Create a cache repository bound to a SQLite database path."""

        self.database_path = database_path

    def get_json(self, cache_key: str) -> dict[str, Any] | list[Any] | None:
        """Return a cached JSON payload when it exists and has not expired."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT payload_json, expires_at
                FROM agent_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if _parse_datetime(row["expires_at"]) <= datetime.now(timezone.utc):
                connection.execute(
                    "DELETE FROM agent_cache WHERE cache_key = ?",
                    (cache_key,),
                )
                connection.commit()
                return None
            return json.loads(row["payload_json"])
        finally:
            connection.close()

    def set_json(
        self,
        *,
        cache_key: str,
        scope: str,
        payload: dict[str, Any] | list[Any],
        expires_at: datetime,
    ) -> None:
        """Persist a JSON payload until the given expiry timestamp."""

        now = datetime.now(timezone.utc)
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO agent_cache (
                    cache_key,
                    scope,
                    payload_json,
                    expires_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    scope = excluded.scope,
                    payload_json = excluded.payload_json,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (
                    cache_key,
                    scope,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    _to_utc(expires_at).isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def delete_expired(self) -> int:
        """Delete expired cache rows and return the deleted row count."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            cursor = connection.execute(
                "DELETE FROM agent_cache WHERE expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _to_utc(parsed)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
