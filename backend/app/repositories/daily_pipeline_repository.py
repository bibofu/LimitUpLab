"""SQLite persistence for automated daily close-loop executions."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import DailyPipelineRun


class SQLiteDailyPipelineRepository:
    """Store and query after-close pipeline execution history."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save_run(self, run: DailyPipelineRun) -> None:
        """Insert or update one pipeline run as its status changes."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO daily_pipeline_runs (
                    run_id, trade_date, trigger, status, attempt_count,
                    report_json, error_message, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    trigger = excluded.trigger,
                    status = excluded.status,
                    attempt_count = excluded.attempt_count,
                    report_json = excluded.report_json,
                    error_message = excluded.error_message,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at
                """,
                self._to_record(run),
            )
            connection.commit()
        finally:
            connection.close()

    def list_recent(self, limit: int = 10) -> list[DailyPipelineRun]:
        """Return recent pipeline runs, newest first."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM daily_pipeline_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [self._from_row(row) for row in rows]

    def latest_for_date(self, trade_date: date) -> DailyPipelineRun | None:
        """Return the newest execution for one target trade date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT *
                FROM daily_pipeline_runs
                WHERE trade_date = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (trade_date.isoformat(),),
            ).fetchone()
        finally:
            connection.close()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _to_record(run: DailyPipelineRun) -> tuple[object, ...]:
        return (
            run.run_id,
            run.trade_date.isoformat(),
            run.trigger,
            run.status,
            run.attempt_count,
            json.dumps(run.report, ensure_ascii=False, default=str)
            if run.report is not None
            else None,
            run.error_message,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else None,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DailyPipelineRun:
        return DailyPipelineRun(
            run_id=row["run_id"],
            trade_date=date.fromisoformat(row["trade_date"]),
            trigger=row["trigger"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            report=json.loads(row["report_json"]) if row["report_json"] else None,
            error_message=row["error_message"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
        )
