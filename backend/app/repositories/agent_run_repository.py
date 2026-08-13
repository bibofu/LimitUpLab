"""SQLite persistence for Agent execution traces."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import AgentRun


class SQLiteAgentRunRepository:
    """Repository for persisted Agent run records."""

    def __init__(self, database_path: Path | None = None):
        """Create a repository bound to a SQLite database path."""

        self.database_path = database_path

    def save_run(self, run: AgentRun) -> None:
        """Insert or replace one Agent execution trace."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id,
                    session_id,
                    run_type,
                    status,
                    intent,
                    tool_calls_json,
                    input_json,
                    output_json,
                    error_message,
                    started_at,
                    finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    run_type = excluded.run_type,
                    status = excluded.status,
                    intent = excluded.intent,
                    tool_calls_json = excluded.tool_calls_json,
                    input_json = excluded.input_json,
                    output_json = excluded.output_json,
                    error_message = excluded.error_message,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at
                """,
                self._run_to_record(run),
            )
            connection.commit()
        finally:
            connection.close()

    def list_recent_runs(self, session_id: str, limit: int = 10) -> list[AgentRun]:
        """Return recent runs for a chat session, newest first."""

        return self.list_runs(session_id=session_id, limit=limit)

    def list_runs(self, session_id: str | None = None, limit: int = 10) -> list[AgentRun]:
        """Return recent runs, optionally scoped to one session."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            where_clause = "WHERE session_id = ?" if session_id else ""
            params: tuple[object, ...] = (session_id, limit) if session_id else (limit,)
            rows = connection.execute(
                f"""
                SELECT *
                FROM agent_runs
                {where_clause}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        finally:
            connection.close()

        return [self._run_from_row(row) for row in rows]

    def _run_to_record(self, run: AgentRun) -> tuple[object, ...]:
        """Serialize an Agent run for SQLite."""

        return (
            run.run_id,
            run.session_id,
            run.run_type,
            run.status,
            run.intent,
            json.dumps(run.tool_calls, ensure_ascii=False),
            json.dumps(run.input_json, ensure_ascii=False, default=str),
            json.dumps(run.output_json, ensure_ascii=False, default=str)
            if run.output_json is not None
            else None,
            run.error_message,
            run.started_at.isoformat(),
            run.finished_at.isoformat(),
        )

    def _run_from_row(self, row: sqlite3.Row) -> AgentRun:
        """Deserialize a SQLite row into an Agent run model."""

        return AgentRun(
            run_id=row["run_id"],
            session_id=row["session_id"],
            run_type=row["run_type"],
            status=row["status"],
            intent=row["intent"],
            tool_calls=json.loads(row["tool_calls_json"]),
            input_json=json.loads(row["input_json"]),
            output_json=json.loads(row["output_json"]) if row["output_json"] else None,
            error_message=row["error_message"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
        )
