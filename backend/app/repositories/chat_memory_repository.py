"""Read-only access to pre-Graph summaries for one-time conversation import.

The legacy table remains user data. All new summary progress and persistence
belong to the fenced Graph State; there is intentionally no writer here.
"""

import json
from datetime import datetime

from app.database import connect_readonly
from app.models import ChatSessionMemory


class ChatMemoryRepository:
    """Read an existing legacy snapshot under its original ownership boundary."""

    # Initialize ChatMemoryRepository with the supplied dependencies and per-instance state.
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url

    def get_memory(
        self,
        session_id: str,
        *,
        owner_id: str,
    ) -> ChatSessionMemory | None:
        """Return memory only when the session belongs to the requesting owner."""

        connection = connect_readonly(self.database_url)
        try:
            row = connection.execute(
                """
                SELECT memory.*
                FROM chat_session_memories AS memory
                INNER JOIN chat_sessions AS session
                    ON session.session_id = memory.session_id
                WHERE memory.session_id = ?
                  AND memory.owner_id = ?
                  AND session.owner_id = ?
                  AND session.archived_at IS NULL
                """,
                (session_id, owner_id, owner_id),
            ).fetchone()
        finally:
            connection.close()
        return _memory_from_row(row) if row is not None else None

# Reconstruct the validated session-memory model from a database row.
def _memory_from_row(row) -> ChatSessionMemory:
    return ChatSessionMemory(
        session_id=row["session_id"],
        owner_id=row["owner_id"],
        memory_version=row["memory_version"],
        summary=row["summary"],
        research_goal=row["research_goal"],
        stock_symbols=json.loads(row["stock_symbols_json"] or "[]"),
        topics=json.loads(row["topics_json"] or "[]"),
        date_scope=row["date_scope"],
        constraints=json.loads(row["constraints_json"] or "[]"),
        unresolved_questions=json.loads(
            row["unresolved_questions_json"] or "[]"
        ),
        summarized_message_count=int(row["summarized_message_count"] or 0),
        last_message_id=row["last_message_id"],
        generation_mode=row["generation_mode"],
        model=row["model"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
