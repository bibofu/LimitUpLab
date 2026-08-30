"""SQLite persistence for immutable daily review artifacts."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import (
    DailyReviewSnapshot,
    DailyReviewSnapshotSummary,
    ReviewAgentReportResponse,
)


class SQLiteReviewSnapshotRepository:
    """Store one review report per market data cutoff date."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save_snapshot(self, snapshot: DailyReviewSnapshot) -> None:
        """Insert a snapshot once and preserve its original contents on reruns."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO daily_review_snapshots (
                    as_of_date, start_date, report_json, generated_by, generated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(as_of_date) DO NOTHING
                """,
                (
                    snapshot.as_of_date.isoformat(),
                    snapshot.start_date.isoformat(),
                    snapshot.report.model_dump_json(),
                    snapshot.generated_by,
                    snapshot.generated_at.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_snapshot(self, as_of_date: date) -> DailyReviewSnapshot | None:
        """Return one persisted review artifact by its data cutoff date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT *
                FROM daily_review_snapshots
                WHERE as_of_date = ?
                """,
                (as_of_date.isoformat(),),
            ).fetchone()
        finally:
            connection.close()
        return self._from_row(row) if row is not None else None

    def list_summaries(self, limit: int = 20) -> list[DailyReviewSnapshotSummary]:
        """Return newest persisted review dates without loading every full report."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM daily_review_snapshots
                ORDER BY as_of_date DESC
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        finally:
            connection.close()
        return [self._summary_from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DailyReviewSnapshot:
        report = ReviewAgentReportResponse.model_validate_json(row["report_json"])
        return DailyReviewSnapshot(
            as_of_date=date.fromisoformat(row["as_of_date"]),
            start_date=date.fromisoformat(row["start_date"]),
            report=report,
            generated_by=row["generated_by"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
        )

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> DailyReviewSnapshotSummary:
        payload = json.loads(row["report_json"])
        return DailyReviewSnapshotSummary(
            as_of_date=date.fromisoformat(row["as_of_date"]),
            start_date=date.fromisoformat(row["start_date"]),
            sample_size=int(payload.get("sample_size") or 0),
            outcome_ready_count=(
                int(payload.get("sample_size") or 0)
                - int(payload.get("pending_count") or 0)
            ),
            top_pick_promotion_rate=payload.get("top_pick_promotion_rate"),
            market_promotion_rate=payload.get("market_promotion_rate"),
            generated_by=row["generated_by"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
        )
