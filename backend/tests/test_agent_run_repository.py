import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import AgentRun
from app.repositories import SQLiteAgentRunRepository


class AgentRunRepositoryTest(unittest.TestCase):
    def test_save_and_list_recent_runs(self) -> None:
        database_path = Path(__file__).resolve().parents[1] / ".test_agent_runs.sqlite"
        if database_path.exists():
            database_path.unlink()
        try:
            repository = SQLiteAgentRunRepository(database_path)
            run = AgentRun(
                run_id="run_test",
                session_id="session-a",
                run_type="agent_chat",
                status="success",
                intent="rating_explain",
                tool_calls=["first_board_ratings"],
                input_json={"message": "why", "symbol": "301489"},
                output_json={"answer": "ok"},
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )

            repository.save_run(run)
            rows = repository.list_recent_runs("session-a")
        finally:
            if database_path.exists():
                database_path.unlink()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].run_id, "run_test")
        self.assertEqual(rows[0].tool_calls, ["first_board_ratings"])
        self.assertEqual(rows[0].input_json["symbol"], "301489")


if __name__ == "__main__":
    unittest.main()
