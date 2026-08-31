"""SQLite persistence for half-hour recommendation intelligence snapshots."""

from __future__ import annotations

from pathlib import Path

from app.database import connect, initialize_database
from app.models import RecommendationIntelligenceResponse


class SQLiteRecommendationIntelligenceRepository:
    """Store recent mutable evidence without rewriting prediction snapshots."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save(
        self,
        response: RecommendationIntelligenceResponse,
        *,
        retain: int = 96,
    ) -> None:
        """Persist one refresh and retain a bounded diagnostic history."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO recommendation_intelligence_snapshots (
                    refresh_id, refreshed_at, status, response_json
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(refresh_id) DO UPDATE SET
                    refreshed_at = excluded.refreshed_at,
                    status = excluded.status,
                    response_json = excluded.response_json
                """,
                (
                    response.refresh_id,
                    response.refreshed_at.isoformat(),
                    response.status,
                    response.model_dump_json(),
                ),
            )
            connection.execute(
                """
                DELETE FROM recommendation_intelligence_snapshots
                WHERE refresh_id NOT IN (
                    SELECT refresh_id
                    FROM recommendation_intelligence_snapshots
                    ORDER BY refreshed_at DESC
                    LIMIT ?
                )
                """,
                (max(1, min(retain, 500)),),
            )
            connection.commit()
        finally:
            connection.close()

    def get_latest(self) -> RecommendationIntelligenceResponse | None:
        """Return the newest persisted refresh."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM recommendation_intelligence_snapshots
                ORDER BY refreshed_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        return (
            RecommendationIntelligenceResponse.model_validate_json(
                row["response_json"]
            )
            if row
            else None
        )
