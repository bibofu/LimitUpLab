"""SQLite persistence for owner-scoped rolling chat memory."""

import json
from datetime import datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import ChatSessionMemory
from app.repositories.chat_session_repository import SessionOwnershipError


class SQLiteChatMemoryRepository:
    """Persist one compact rolling-memory snapshot per chat session."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def get_memory(
        self,
        session_id: str,
        *,
        owner_id: str,
    ) -> ChatSessionMemory | None:
        """Return memory only when the session belongs to the requesting owner."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
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

    def save_memory(self, memory: ChatSessionMemory) -> ChatSessionMemory:
        """Upsert memory after verifying session ownership."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            session = connection.execute(
                """
                SELECT owner_id
                FROM chat_sessions
                WHERE session_id = ? AND archived_at IS NULL
                """,
                (memory.session_id,),
            ).fetchone()
            if session is None or session["owner_id"] != memory.owner_id:
                raise SessionOwnershipError(
                    "Chat memory belongs to another visitor or missing session."
                )
            connection.execute(
                """
                INSERT INTO chat_session_memories (
                    session_id, owner_id, memory_version, summary, research_goal,
                    stock_symbols_json, topics_json, date_scope, constraints_json,
                    unresolved_questions_json, summarized_message_count,
                    last_message_id, generation_mode, model, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    memory_version = excluded.memory_version,
                    summary = excluded.summary,
                    research_goal = excluded.research_goal,
                    stock_symbols_json = excluded.stock_symbols_json,
                    topics_json = excluded.topics_json,
                    date_scope = excluded.date_scope,
                    constraints_json = excluded.constraints_json,
                    unresolved_questions_json = excluded.unresolved_questions_json,
                    summarized_message_count = excluded.summarized_message_count,
                    last_message_id = excluded.last_message_id,
                    generation_mode = excluded.generation_mode,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                _memory_record(memory),
            )
            connection.commit()
        finally:
            connection.close()
        saved = self.get_memory(memory.session_id, owner_id=memory.owner_id)
        if saved is None:
            raise RuntimeError("Failed to persist chat session memory.")
        return saved


def _memory_record(memory: ChatSessionMemory) -> tuple[object, ...]:
    return (
        memory.session_id,
        memory.owner_id,
        memory.memory_version,
        memory.summary,
        memory.research_goal,
        json.dumps(memory.stock_symbols, ensure_ascii=False),
        json.dumps(memory.topics, ensure_ascii=False),
        memory.date_scope,
        json.dumps(memory.constraints, ensure_ascii=False),
        json.dumps(memory.unresolved_questions, ensure_ascii=False),
        memory.summarized_message_count,
        memory.last_message_id,
        memory.generation_mode,
        memory.model,
        memory.created_at.isoformat(),
        memory.updated_at.isoformat(),
    )


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
