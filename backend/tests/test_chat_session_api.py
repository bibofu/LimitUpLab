import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.models import (
    ChatSessionCreateRequest,
    ChatSessionMessage,
    ChatSessionUpdateRequest,
)
from app.repositories import SQLiteChatSessionRepository
from app.routers.agents import (
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_chat_sessions,
    update_chat_session,
)


class ChatSessionApiTest(unittest.TestCase):
    def test_session_lifecycle_and_message_restore(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f".test_chat_session_api_{uuid4().hex}.sqlite"
        )
        self.addCleanup(database_path.unlink, missing_ok=True)
        with patch.dict(
            os.environ,
            {"LIMITUPLAB_DATABASE_PATH": str(database_path)},
            clear=False,
        ):
            created = create_chat_session(ChatSessionCreateRequest())
            session_id = created.session_id

            SQLiteChatSessionRepository(database_path).append_message(
                ChatSessionMessage(
                    message_id="message-api",
                    session_id=session_id,
                    role="user",
                    content="恢复这条历史消息",
                    created_at=datetime.now(timezone.utc),
                )
            )

            sessions = list_chat_sessions(limit=30)
            detail = get_chat_session(session_id)
            renamed = update_chat_session(
                session_id,
                ChatSessionUpdateRequest(title="今日首板复盘"),
            )
            deleted = delete_chat_session(session_id)
            with self.assertRaises(HTTPException) as missing_error:
                get_chat_session(session_id)

        self.assertEqual(sessions.sessions[0].message_count, 1)
        self.assertEqual(detail.messages[0].content, "恢复这条历史消息")
        self.assertEqual(renamed.title, "今日首板复盘")
        self.assertEqual(deleted, {"deleted": True})
        self.assertEqual(missing_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
