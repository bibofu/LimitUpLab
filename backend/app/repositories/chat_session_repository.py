"""SQLite persistence for resumable Agent chat sessions and messages."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.database import connect, initialize_database
from app.models import (
    AgentToolTrace,
    ChatSessionDetail,
    ChatSessionMessage,
    ChatSessionSummary,
    extract_agent_stock_mentions,
)


LOCAL_OWNER_ID = "local-user"
DEFAULT_SESSION_TITLE = "新对话"


class SessionOwnershipError(PermissionError):
    """Raised when a session id already belongs to another owner."""


class SQLiteChatSessionRepository:
    """Store user-facing conversations separately from Agent execution traces."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def create_session(
        self,
        *,
        title: str | None = None,
        owner_id: str = LOCAL_OWNER_ID,
        session_id: str | None = None,
    ) -> ChatSessionDetail:
        """Create and return an empty active conversation."""

        resolved_session_id = session_id or f"chat_{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            existing = connection.execute(
                "SELECT owner_id FROM chat_sessions WHERE session_id = ?",
                (resolved_session_id,),
            ).fetchone()
            if existing is not None and existing["owner_id"] != owner_id:
                raise SessionOwnershipError("Chat session belongs to another visitor.")
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_sessions (
                    session_id, owner_id, title, created_at, updated_at, archived_at
                )
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    resolved_session_id,
                    owner_id,
                    _normalize_title(title),
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        session = self.get_session(resolved_session_id, owner_id=owner_id)
        if session is None:
            raise RuntimeError("Failed to create chat session.")
        return session

    def ensure_session(
        self,
        session_id: str,
        *,
        first_message: str | None = None,
        owner_id: str = LOCAL_OWNER_ID,
    ) -> ChatSessionDetail:
        """Return an existing session or create one for legacy chat clients."""

        existing = self.get_session(session_id, owner_id=owner_id)
        if existing is not None:
            return existing
        return self.create_session(
            session_id=session_id,
            owner_id=owner_id,
            title=_title_from_message(first_message) if first_message else None,
        )

    def list_sessions(
        self,
        *,
        owner_id: str = LOCAL_OWNER_ID,
        limit: int = 30,
    ) -> list[ChatSessionSummary]:
        """Return active conversations ordered by latest message activity."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT
                    session.*,
                    (
                        SELECT COUNT(*)
                        FROM chat_messages AS message
                        WHERE message.session_id = session.session_id
                    ) AS message_count,
                    (
                        SELECT message.content
                        FROM chat_messages AS message
                        WHERE message.session_id = session.session_id
                        ORDER BY message.created_at DESC, message.rowid DESC
                        LIMIT 1
                    ) AS last_message_preview
                FROM chat_sessions AS session
                WHERE session.owner_id = ? AND session.archived_at IS NULL
                ORDER BY session.updated_at DESC
                LIMIT ?
                """,
                (owner_id, max(1, min(limit, 100))),
            ).fetchall()
        finally:
            connection.close()
        return [_session_summary_from_row(row) for row in rows]

    def get_session(
        self,
        session_id: str,
        *,
        owner_id: str = LOCAL_OWNER_ID,
        message_limit: int = 100,
    ) -> ChatSessionDetail | None:
        """Return one active conversation and its most recent ordered messages."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT
                    session.*,
                    (
                        SELECT COUNT(*)
                        FROM chat_messages AS message
                        WHERE message.session_id = session.session_id
                    ) AS message_count,
                    (
                        SELECT message.content
                        FROM chat_messages AS message
                        WHERE message.session_id = session.session_id
                        ORDER BY message.created_at DESC, message.rowid DESC
                        LIMIT 1
                    ) AS last_message_preview
                FROM chat_sessions AS session
                WHERE session.session_id = ?
                  AND session.owner_id = ?
                  AND session.archived_at IS NULL
                """,
                (session_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            message_rows = connection.execute(
                """
                SELECT *
                FROM (
                    SELECT chat_messages.*, rowid AS message_rowid
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC, message_rowid ASC
                """,
                (session_id, max(1, min(message_limit, 200))),
            ).fetchall()
        finally:
            connection.close()
        return ChatSessionDetail(
            **_session_summary_from_row(row).model_dump(),
            messages=[_message_from_row(item) for item in message_rows],
        )

    def append_message(
        self,
        message: ChatSessionMessage,
        *,
        owner_id: str = LOCAL_OWNER_ID,
    ) -> ChatSessionMessage:
        """Persist one idempotent message and advance session activity time."""

        self.ensure_session(
            message.session_id,
            first_message=message.content if message.role == "user" else None,
            owner_id=owner_id,
        )
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_messages (
                    message_id, session_id, role, content, status,
                    run_id, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.status,
                    message.run_id,
                    json.dumps(message.metadata, ensure_ascii=False, default=str),
                    message.created_at.isoformat(),
                ),
            )
            title = _title_from_message(message.content)
            connection.execute(
                """
                UPDATE chat_sessions
                SET
                    title = CASE
                        WHEN title = ? AND ? = 'user' THEN ?
                        ELSE title
                    END,
                    updated_at = ?
                WHERE session_id = ? AND owner_id = ?
                """,
                (
                    DEFAULT_SESSION_TITLE,
                    message.role,
                    title,
                    message.created_at.isoformat(),
                    message.session_id,
                    owner_id,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM chat_messages WHERE message_id = ?",
                (message.message_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("Failed to persist chat message.")
        return _message_from_row(row)

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        owner_id: str = LOCAL_OWNER_ID,
    ) -> ChatSessionDetail | None:
        """Rename one active conversation."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                UPDATE chat_sessions
                SET title = ?, updated_at = ?
                WHERE session_id = ? AND owner_id = ? AND archived_at IS NULL
                """,
                (
                    _normalize_title(title),
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                    owner_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_session(session_id, owner_id=owner_id)

    def delete_session(
        self,
        session_id: str,
        *,
        owner_id: str = LOCAL_OWNER_ID,
    ) -> bool:
        """Permanently delete one conversation, its messages, and run traces."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            session = connection.execute(
                """
                SELECT session_id
                FROM chat_sessions
                WHERE session_id = ? AND owner_id = ?
                """,
                (session_id, owner_id),
            ).fetchone()
            if session is None:
                return False
            with connection:
                connection.execute(
                    "DELETE FROM chat_messages WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM agent_runs WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM chat_sessions WHERE session_id = ? AND owner_id = ?",
                    (session_id, owner_id),
                )
            return True
        finally:
            connection.close()


def _normalize_title(title: str | None) -> str:
    normalized = " ".join((title or "").split()).strip()
    return normalized[:80] or DEFAULT_SESSION_TITLE


def _title_from_message(message: str | None) -> str:
    normalized = " ".join((message or "").split()).strip()
    if not normalized:
        return DEFAULT_SESSION_TITLE
    return normalized if len(normalized) <= 28 else f"{normalized[:28]}..."


def _session_summary_from_row(row: sqlite3.Row) -> ChatSessionSummary:
    preview = str(row["last_message_preview"] or "").strip()
    return ChatSessionSummary(
        session_id=row["session_id"],
        owner_id=row["owner_id"],
        title=row["title"],
        message_count=int(row["message_count"] or 0),
        last_message_preview=preview[:100] or None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> ChatSessionMessage:
    metadata = json.loads(row["metadata_json"] or "{}")
    if (
        row["role"] == "assistant"
        and not metadata.get("stock_mentions")
        and isinstance(metadata.get("tool_results"), list)
    ):
        try:
            traces = [
                AgentToolTrace.model_validate(item)
                for item in metadata["tool_results"]
            ]
            metadata["stock_mentions"] = [
                item.model_dump(mode="json")
                for item in extract_agent_stock_mentions(row["content"], traces)
            ]
        except (TypeError, ValueError):
            metadata["stock_mentions"] = []
    return ChatSessionMessage(
        message_id=row["message_id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        status=row["status"],
        run_id=row["run_id"],
        metadata=metadata,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
