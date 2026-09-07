import os
import unittest
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from app.models import (
    AgentPrediction,
    DailyReviewSnapshot,
    ReviewAgentPick,
    ReviewAgentReportResponse,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteReviewSnapshotRepository
from app.services.daily_review import resolve_review_start_date, review_snapshot_matches_current_predictions


TEST_TMP_ROOT = Path(
    os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1])
)


class ReviewSnapshotRepositoryTest(unittest.TestCase):
    def test_review_window_finds_five_mature_prediction_dates_across_market_gaps(self) -> None:
        available_dates = [
            date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1),
            date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7),
        ]
        prediction_dates = [
            date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1),
            date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7),
        ]
        predictions = [
            AgentPrediction(
                prediction_id=f"historical-{trade_date.isoformat()}",
                trade_date=trade_date,
                symbol="000001",
                name="测试股份",
                score=88,
                rating="A",
                confidence=0.8,
                scoring_version="review-window-v1",
                prediction_source="historical_backtest",
                data_as_of=trade_date,
                facts_json={},
                reasons=[],
                risks=[],
                created_at=datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc),
            )
            for trade_date in prediction_dates
        ]
        repository = Mock(spec=SQLiteFirstBoardRepository)
        repository.list_predictions_between.return_value = predictions

        result = resolve_review_start_date(
            available_dates=available_dates,
            as_of_date=date(2026, 9, 7),
            first_board_repository=repository,
        )

        self.assertEqual(result, date(2026, 8, 28))

    def test_audit_annotation_does_not_rewrite_saved_report(self) -> None:
        from app.services.prediction_time_audit import content_hash
        snapshot = self._snapshot(date(2026, 9, 3), finding="original")
        repository = SQLiteReviewSnapshotRepository(self.database_path)
        repository.save_snapshot(snapshot)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            original = connection.execute("SELECT report_json FROM daily_review_snapshots").fetchone()[0]
            connection.execute("INSERT INTO prediction_time_audits VALUES ('review', ?, ?, ?, ?)",
                ("2026-09-03", content_hash(original), json.dumps({"status": "excluded"}), "2026-09-07T01:00:00+08:00"))
        loaded = repository.get_snapshot(date(2026, 9, 3))
        self.assertEqual(loaded.report.time_audit_status, "excluded")
        self.assertTrue(loaded.report.warnings)
        self.assertIsNone(repository.list_summaries()[0].top_pick_promotion_rate)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT report_json FROM daily_review_snapshots").fetchone()[0], original)

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

    def test_stale_snapshot_is_detected_after_official_prediction_arrives(self) -> None:
        trade_date = date(2026, 8, 31)
        data_as_of = date(2026, 9, 1)
        prediction = AgentPrediction(
            prediction_id="close-live-000001",
            trade_date=trade_date,
            symbol="000001",
            name="测试股份",
            score=88,
            rating="A",
            confidence=0.8,
            scoring_version="close-live-v1",
            prediction_source="live",
            data_as_of=trade_date,
            facts_json={},
            reasons=[],
            risks=[],
            created_at=datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc),
        )
        report = self._snapshot(data_as_of, finding="快照早于竞价终选").report
        first_board_repository = SQLiteFirstBoardRepository(self.database_path)

        with patch.object(
            first_board_repository,
            "list_predictions_between",
            return_value=[prediction],
        ):
            self.assertFalse(
                review_snapshot_matches_current_predictions(
                    report=report,
                    first_board_repository=first_board_repository,
                )
            )

            current_report = report.model_copy(
                update={
                    "sample_size": 1,
                    "time_audit_status": "checked",
                    "reviewed_picks": [
                        ReviewAgentPick(
                            trade_date=trade_date,
                            symbol=prediction.symbol,
                            name=prediction.name,
                            score=prediction.score,
                            rating=prediction.rating,
                            confidence=prediction.confidence,
                            prediction_source=prediction.prediction_source,
                            data_as_of=prediction.data_as_of,
                            time_cohort="legacy_close",
                            scoring_version=prediction.scoring_version,
                            evaluation_label="pending",
                            outcome_ready=False,
                            promoted_to_second_board=False,
                        )
                    ],
                }
            )
            self.assertTrue(
                review_snapshot_matches_current_predictions(
                    report=current_report,
                    first_board_repository=first_board_repository,
                )
            )

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
