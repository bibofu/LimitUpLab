import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models import DailyPipelineRun
from app.repositories import (
    SQLiteDailyPipelineRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
)
from scripts.run_daily_close_loop import (
    DailyCloseLoopLock,
    execute_daily_close_loop,
)
from scripts.update_daily_data import DailyUpdateReport


TEST_TMP_ROOT = Path(
    os.getenv(
        "LIMITUPLAB_TEST_TMP",
        Path(__file__).resolve().parents[1],
    )
)
CN_TZ = ZoneInfo("Asia/Shanghai")


class DailyCloseLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        token = uuid4().hex
        self.database_path = TEST_TMP_ROOT / f"daily-close-loop-{token}.sqlite"
        self.lock_path = TEST_TMP_ROOT / f"daily-close-loop-{token}.lock"
        self.report_path = TEST_TMP_ROOT / f"daily-close-loop-{token}.json"
        self.alert_path = TEST_TMP_ROOT / f"daily-close-loop-{token}-alert.json"
        self.run_repository = SQLiteDailyPipelineRepository(self.database_path)
        self.limit_repository = SQLiteLimitUpRepository(
            database_path=self.database_path,
            seed_if_empty=False,
        )
        self.first_board_repository = SQLiteFirstBoardRepository(self.database_path)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
            self.lock_path,
            self.report_path,
            self.report_path.with_name(f"{self.report_path.name}.tmp"),
            self.alert_path,
            self.alert_path.with_name(f"{self.alert_path.name}.tmp"),
        ):
            path.unlink(missing_ok=True)

    @staticmethod
    def _calendar(start: date, end: date) -> list[date]:
        day_count = (end - start).days
        return [
            start.fromordinal(start.toordinal() + offset)
            for offset in range(day_count + 1)
            if start.fromordinal(start.toordinal() + offset).weekday() < 5
        ]

    @staticmethod
    def _complete_report(trade_date: date, *, live_count: int) -> DailyUpdateReport:
        return DailyUpdateReport(
            trade_date=trade_date.isoformat(),
            target_candidates_checked=10,
            persisted_top_predictions=10,
            persisted_live_predictions=live_count,
            persisted_historical_predictions=0 if live_count else 10,
            tracked_candidate_references=10,
            tracked_cache_ready=10,
            tracked_cache_missing=0,
            health={
                "status": "healthy",
                "raw_events_ready": True,
                "first_board_features_ready": True,
            },
        )

    def _execute(self, **overrides):
        arguments = {
            "trigger": "scheduled",
            "max_attempts": 1,
            "retry_delay_seconds": 0,
            "lock_path": self.lock_path,
            "report_path": self.report_path,
            "alert_path": self.alert_path,
            "limit_up_repository": self.limit_repository,
            "first_board_repository": self.first_board_repository,
            "run_repository": self.run_repository,
            "calendar_collector": self._calendar,
        }
        arguments.update(overrides)
        return execute_daily_close_loop(**arguments)

    def test_same_day_after_close_persists_live_prediction(self) -> None:
        target_date = date(2026, 8, 21)
        received: list[dict[str, object]] = []

        def fake_update(**kwargs) -> DailyUpdateReport:
            received.append(kwargs)
            return self._complete_report(target_date, live_count=10)

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            update_runner=fake_update,
        )

        self.assertEqual(execution.status, "success")
        self.assertTrue(received[0]["persist_live_prediction"])
        self.assertTrue(self.report_path.exists())
        self.assertFalse(self.alert_path.exists())
        persisted = self.run_repository.latest_for_date(target_date)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, "success")
        self.assertTrue(persisted.report["live_prediction_eligible"])

    def test_late_backfill_cannot_be_labeled_live(self) -> None:
        target_date = date(2026, 8, 20)
        received: list[dict[str, object]] = []

        def fake_update(**kwargs) -> DailyUpdateReport:
            received.append(kwargs)
            return self._complete_report(target_date, live_count=0)

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            update_runner=fake_update,
        )

        self.assertEqual(execution.status, "success")
        self.assertFalse(received[0]["persist_live_prediction"])
        self.assertFalse(execution.run.report["live_prediction_eligible"])

    def test_transient_failure_is_retried_and_audited(self) -> None:
        target_date = date(2026, 8, 21)
        calls = 0
        delays: list[float] = []

        def flaky_update(**_kwargs) -> DailyUpdateReport:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary source failure")
            return self._complete_report(target_date, live_count=10)

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            max_attempts=3,
            retry_delay_seconds=2,
            update_runner=flaky_update,
            sleep_fn=delays.append,
        )

        self.assertEqual(execution.status, "success")
        self.assertEqual(calls, 2)
        self.assertEqual(delays, [2])
        self.assertEqual(execution.run.attempt_count, 2)

    def test_incomplete_cache_writes_partial_alert(self) -> None:
        target_date = date(2026, 8, 21)

        def incomplete_update(**_kwargs) -> DailyUpdateReport:
            report = self._complete_report(target_date, live_count=10)
            report.tracked_cache_missing = 2
            return report

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            max_attempts=2,
            update_runner=incomplete_update,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(execution.status, "partial")
        self.assertEqual(execution.exit_code, 2)
        self.assertEqual(execution.run.attempt_count, 2)
        self.assertTrue(self.alert_path.exists())
        self.assertIn("lack available bars", execution.run.error_message)

    def test_incomplete_outcome_maturity_writes_partial_alert(self) -> None:
        target_date = date(2026, 8, 21)

        def incomplete_update(**_kwargs) -> DailyUpdateReport:
            report = self._complete_report(target_date, live_count=10)
            report.tracked_next_day_outcomes_expected = 10
            report.tracked_next_day_outcomes_ready = 9
            return report

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            update_runner=incomplete_update,
        )

        self.assertEqual(execution.status, "partial")
        self.assertEqual(execution.exit_code, 2)
        self.assertTrue(self.alert_path.exists())
        self.assertIn("D+1 outcomes are incomplete", execution.run.error_message)

    def test_existing_live_snapshot_is_valid_during_outcome_retry(self) -> None:
        target_date = date(2026, 8, 21)

        def retry_update(**_kwargs) -> DailyUpdateReport:
            report = self._complete_report(target_date, live_count=0)
            report.live_prediction_snapshot_ready = True
            return report

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
            update_runner=retry_update,
        )

        self.assertEqual(execution.status, "success")
        self.assertEqual(execution.exit_code, 0)

    def test_successful_date_is_idempotently_skipped(self) -> None:
        target_date = date(2026, 8, 21)
        completed = DailyPipelineRun(
            run_id="daily_existing",
            trade_date=target_date,
            trigger="scheduled",
            status="success",
            attempt_count=1,
            report={"pipeline": {}},
            started_at=datetime(2026, 8, 21, 8, 10, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 21, 8, 12, tzinfo=timezone.utc),
        )
        self.run_repository.save_run(completed)

        def unexpected_update(**_kwargs) -> DailyUpdateReport:
            self.fail("already successful date must not run again")

        execution = self._execute(
            requested_date=target_date,
            now=datetime(2026, 8, 21, 16, 20, tzinfo=CN_TZ),
            update_runner=unexpected_update,
        )

        self.assertEqual(execution.status, "skipped")
        self.assertEqual(execution.run.run_id, "daily_existing")

    def test_existing_lock_prevents_overlapping_run(self) -> None:
        with DailyCloseLoopLock(self.lock_path):
            execution = self._execute(
                requested_date=date(2026, 8, 21),
                now=datetime(2026, 8, 21, 16, 10, tzinfo=CN_TZ),
                update_runner=lambda **_kwargs: self.fail("must stay locked"),
            )

        self.assertEqual(execution.status, "skipped")
        self.assertIsNone(execution.run)
        self.assertIn("already running", execution.message)


if __name__ == "__main__":
    unittest.main()
