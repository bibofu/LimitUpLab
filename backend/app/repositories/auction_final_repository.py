"""SQLite persistence for immutable 09:25 auction-final recommendations."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.database import connect, initialize_database
from app.models import AuctionFinalRecommendationsResponse


class SQLiteAuctionFinalRepository:
    """Store one immutable final recommendation batch per date and version."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save(self, response: AuctionFinalRecommendationsResponse) -> bool:
        """Insert once so reruns cannot rewrite the morning's final decision."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            before = connection.total_changes
            connection.execute(
                """
                INSERT INTO auction_final_recommendation_snapshots (
                    trade_date, scoring_version, finalized_at, status, response_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, scoring_version) DO NOTHING
                """,
                (
                    response.trade_date.isoformat(),
                    response.scoring_version,
                    response.finalized_at.isoformat(),
                    response.status,
                    response.model_dump_json(),
                ),
            )
            connection.commit()
            return connection.total_changes > before
        finally:
            connection.close()

    def get(self, trade_date: date) -> AuctionFinalRecommendationsResponse | None:
        """Return the newest final snapshot for one trading day."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM auction_final_recommendation_snapshots
                WHERE trade_date = ?
                ORDER BY finalized_at DESC
                LIMIT 1
                """,
                (trade_date.isoformat(),),
            ).fetchone()
        finally:
            connection.close()
        return (
            AuctionFinalRecommendationsResponse.model_validate_json(
                row["response_json"]
            )
            if row
            else None
        )

    def get_latest(self) -> AuctionFinalRecommendationsResponse | None:
        """Return the newest persisted auction-final snapshot."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM auction_final_recommendation_snapshots
                ORDER BY trade_date DESC, finalized_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        return (
            AuctionFinalRecommendationsResponse.model_validate_json(
                row["response_json"]
            )
            if row
            else None
        )
