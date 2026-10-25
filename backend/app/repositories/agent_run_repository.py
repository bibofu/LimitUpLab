"""Read-only access to delivered and historical Agent execution records."""

import json
from app.storage.postgres import Record
from datetime import datetime

from app.database import connect, initialize_database
from app.models import AgentRun


class AgentRunRepository:
    """Read persisted runs; RunDelivery alone publishes new chat run records."""

    def __init__(self, database_url: str | None = None):
        """Create a repository bound to a PostgreSQL URL."""

        self.database_url = database_url

    def list_recent_runs(
        self,
        session_id: str,
        limit: int = 10,
        *,
        owner_id: str | None = None,
    ) -> list[AgentRun]:
        """Return recent runs for a chat session, newest first."""

        return self.list_runs(
            session_id=session_id,
            limit=limit,
            owner_id=owner_id,
        )

    def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 10,
        *,
        owner_id: str | None = None,
    ) -> list[AgentRun]:
        """Return recent runs, optionally scoped to one session."""

        connection = connect(self.database_url)
        try:
            initialize_database(connection)
            conditions: list[str] = []
            params: list[object] = []
            join_clause = ""
            if owner_id is not None:
                join_clause = (
                    "INNER JOIN chat_sessions AS session "
                    "ON session.session_id = run.session_id"
                )
                conditions.append("session.owner_id = ?")
                params.append(owner_id)
            if session_id:
                conditions.append("run.session_id = ?")
                params.append(session_id)
            where_clause = (
                f"WHERE {' AND '.join(conditions)}" if conditions else ""
            )
            params.append(limit)
            rows = connection.execute(
                f"""
                SELECT run.*
                FROM agent_runs AS run
                {join_clause}
                {where_clause}
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            connection.close()

        return [self._run_from_row(row) for row in rows]

    def _run_from_row(self, row: Record) -> AgentRun:
        """Deserialize a PostgreSQL row into an Agent run model."""

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
