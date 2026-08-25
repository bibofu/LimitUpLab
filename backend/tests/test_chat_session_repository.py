import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import connect
from app.models import AgentRun, ChatSessionMessage
from app.repositories import SQLiteAgentRunRepository, SQLiteChatSessionRepository


class ChatSessionRepositoryTest(unittest.TestCase):
    def test_create_resume_rename_and_delete_session(self) -> None:
        database_path = Path(__file__).resolve().parents[1] / ".test_chat_sessions.sqlite"
        if database_path.exists():
            database_path.unlink()
        repository = SQLiteChatSessionRepository(database_path)
        run_repository = SQLiteAgentRunRepository(database_path)
        started_at = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
        try:
            created = repository.create_session(session_id="session-a")
            repository.append_message(
                ChatSessionMessage(
                    message_id="message-user",
                    session_id=created.session_id,
                    role="user",
                    content="中电鑫龙为什么评分高",
                    created_at=started_at,
                )
            )
            run_repository.save_run(
                AgentRun(
                    run_id="run-1",
                    session_id=created.session_id,
                    run_type="chat",
                    status="success",
                    intent="rating_explain",
                    input_json={"message": "中电鑫龙为什么评分高"},
                    output_json={"answer": "评分由封板质量和市场环境共同决定。"},
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=1),
                )
            )
            repository.append_message(
                ChatSessionMessage(
                    message_id="message-agent",
                    session_id=created.session_id,
                    role="assistant",
                    content="评分由封板质量和市场环境共同决定。",
                    run_id="run-1",
                    metadata={"intent": "rating_explain"},
                    created_at=started_at + timedelta(seconds=1),
                )
            )
            repository.append_message(
                ChatSessionMessage(
                    message_id="message-agent",
                    session_id=created.session_id,
                    role="assistant",
                    content="重复消息不会覆盖原消息。",
                    created_at=started_at + timedelta(seconds=2),
                )
            )

            sessions = repository.list_sessions()
            detail = repository.get_session(created.session_id)
            renamed = repository.rename_session(created.session_id, "评分研究")
            deleted = repository.delete_session(created.session_id)
            remaining = repository.list_sessions()
            deleted_detail = repository.get_session(created.session_id)
            remaining_runs = run_repository.list_recent_runs(created.session_id)
            connection = connect(database_path)
            try:
                remaining_message_count = connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
                    (created.session_id,),
                ).fetchone()[0]
            finally:
                connection.close()
        finally:
            if database_path.exists():
                database_path.unlink()

        self.assertEqual(created.title, "新对话")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "中电鑫龙为什么评分高")
        self.assertEqual(sessions[0].message_count, 2)
        self.assertIsNotNone(detail)
        self.assertEqual([item.role for item in detail.messages], ["user", "assistant"])
        self.assertEqual(detail.messages[1].metadata["intent"], "rating_explain")
        self.assertEqual(renamed.title, "评分研究")
        self.assertTrue(deleted)
        self.assertEqual(remaining, [])
        self.assertIsNone(deleted_detail)
        self.assertEqual(remaining_runs, [])
        self.assertEqual(remaining_message_count, 0)


if __name__ == "__main__":
    unittest.main()
