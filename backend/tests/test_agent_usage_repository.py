import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models import AgentUsageRecord
from app.repositories.agent_usage_repository import SQLiteAgentUsageRepository


class AgentUsageRepositoryTest(unittest.TestCase):
    def test_started_request_counts_and_finished_usage_is_aggregated(self) -> None:
        database_path = Path(__file__).resolve().parents[1] / f"usage-{uuid4().hex}.sqlite"
        try:
            repository = SQLiteAgentUsageRepository(database_path)
            started_at = datetime.now(timezone.utc)
            running = AgentUsageRecord(
                usage_id="usage-1",
                run_id="run-1",
                session_id="session-1",
                owner_id="owner-1",
                ip_hash="hashed-ip",
                started_at=started_at,
            )
            repository.start(running)
            repository.record_rejection(
                AgentUsageRecord(
                    usage_id="usage-rejected",
                    run_id="run-rejected",
                    session_id="session-1",
                    owner_id="owner-1",
                    ip_hash="hashed-ip",
                    status="rejected",
                    started_at=started_at,
                    finished_at=started_at,
                    duration_ms=0,
                    error_message="owner_minute_limit",
                )
            )

            initial = repository.owner_today("owner-1")
            self.assertEqual(initial.request_count, 1)
            self.assertEqual(initial.running_count, 1)
            self.assertEqual(initial.rejected_count, 1)

            repository.finish(
                running.model_copy(
                    update={
                        "status": "success",
                        "model": "deepseek-v4-flash",
                        "llm_call_count": 2,
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "total_tokens": 150,
                        "token_usage_complete": True,
                        "planner_prompt_chars": 400,
                        "answer_prompt_chars": 800,
                        "answer_chars": 60,
                        "estimated_cost_usd": 0.001,
                        "finished_at": datetime.now(timezone.utc),
                        "duration_ms": 250,
                    }
                )
            )

            summary = repository.owner_today("owner-1")
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(summary.running_count, 0)
            self.assertEqual(summary.rejected_count, 1)
            self.assertEqual(summary.llm_call_count, 2)
            self.assertEqual(summary.total_tokens, 150)
            self.assertEqual(summary.token_measured_request_count, 1)
            self.assertAlmostEqual(summary.estimated_cost_usd, 0.001)
        finally:
            database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
