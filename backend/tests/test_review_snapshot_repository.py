import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models import DailyReviewSnapshot, ReviewAgentReportResponse
from app.repositories import SQLiteReviewSnapshotRepository


TEST_TMP_ROOT = Path(
    os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1])
)


class ReviewSnapshotRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.database_path = TEST_TMP_ROOT / f"review-snapshot-{uuid4().hex}.sqlite"
        self.repository = SQLiteReviewSnapshotRepository(self.database_path)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_snapshot_is_immutable_and_listed_as_summary(self) -> None:
        as_of_date = date(2026, 8, 28)
        original = self._snapshot(as_of_date, finding="原始复盘结论")
        replacement = self._snapshot(as_of_date, finding="不应覆盖的结论")

        self.repository.save_snapshot(original)
        self.repository.save_snapshot(replacement)

        persisted = self.repository.get_snapshot(as_of_date)
        summaries = self.repository.list_summaries()

        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.report.main_findings, ["原始复盘结论"])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].as_of_date, as_of_date)
        self.assertEqual(summaries[0].sample_size, 10)
        self.assertEqual(summaries[0].outcome_ready_count, 8)

    @staticmethod
    def _snapshot(as_of_date: date, *, finding: str) -> DailyReviewSnapshot:
        report = ReviewAgentReportResponse(
            start_date=date(2026, 8, 21),
            end_date=as_of_date,
            sample_size=10,
            success_count=5,
            failed_count=3,
            pending_count=2,
            top_pick_promotion_rate=0.2,
            market_promotion_rate=0.15,
            main_findings=[finding],
            confidence=0.8,
            generated_by="review-agent-test",
        )
        return DailyReviewSnapshot(
            as_of_date=as_of_date,
            start_date=report.start_date,
            report=report,
            generated_by="daily-review-snapshot-test",
            generated_at=datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
