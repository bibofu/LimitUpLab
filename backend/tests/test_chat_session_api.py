import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.models import (
    AgentRun,
    ChatSessionCreateRequest,
    ChatSessionMessage,
    ChatSessionUpdateRequest,
)
from app.repositories import SQLiteAgentRunRepository, SQLiteChatSessionRepository
from app.routers.agents import (
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_agent_runs,
    list_chat_sessions,
    update_chat_session,
)


class ChatSessionApiTest(unittest.TestCase):
    def test_session_lifecycle_and_message_restore(self) -> None:
        owner_id = "visitor_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        other_owner_id = "visitor_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
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
            created = create_chat_session(
                ChatSessionCreateRequest(),
                owner_id=owner_id,
            )
            session_id = created.session_id

            SQLiteChatSessionRepository(database_path).append_message(
                ChatSessionMessage(
                    message_id="message-api",
                    session_id=session_id,
                    role="user",
                    content="恢复这条历史消息",
                    created_at=datetime.now(timezone.utc),
                ),
                owner_id=owner_id,
            )

            sessions = list_chat_sessions(owner_id=owner_id, limit=30)
            detail = get_chat_session(session_id, owner_id=owner_id)
            self.assertEqual(
                list_chat_sessions(owner_id=other_owner_id, limit=30).sessions,
                [],
            )
            with self.assertRaises(HTTPException) as foreign_get_error:
                get_chat_session(session_id, owner_id=other_owner_id)
            with self.assertRaises(HTTPException) as foreign_delete_error:
                delete_chat_session(session_id, owner_id=other_owner_id)

            run_repository = SQLiteAgentRunRepository(database_path)
            now = datetime.now(timezone.utc)
            run_repository.save_run(
                AgentRun(
                    run_id="owner-run",
                    session_id=session_id,
                    run_type="agent_chat",
                    status="success",
                    input_json={"message": "private question"},
                    output_json={"answer": "private answer"},
                    started_at=now,
                    finished_at=now,
                )
            )
            self.assertEqual(
                len(list_agent_runs(owner_id=owner_id, limit=10).runs),
                1,
            )
            self.assertEqual(
                list_agent_runs(owner_id=other_owner_id, limit=10).runs,
                [],
            )
            renamed = update_chat_session(
                session_id,
                ChatSessionUpdateRequest(title="今日首板复盘"),
                owner_id=owner_id,
            )
            deleted = delete_chat_session(session_id, owner_id=owner_id)
            with self.assertRaises(HTTPException) as missing_error:
                get_chat_session(session_id, owner_id=owner_id)

        self.assertEqual(sessions.sessions[0].message_count, 1)
        self.assertEqual(detail.messages[0].content, "恢复这条历史消息")
        self.assertEqual(renamed.title, "今日首板复盘")
        self.assertEqual(deleted, {"deleted": True})
        self.assertEqual(missing_error.exception.status_code, 404)
        self.assertEqual(foreign_get_error.exception.status_code, 404)
        self.assertEqual(foreign_delete_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
