import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models import RecommendationIntelligenceResponse
from app.services.recommendation_intelligence import (
    should_finalize_recommendation_intelligence,
)

from scripts.run_recommendation_refresh_loop import (
    RefreshLoopLock,
    _inside_premarket_catch_up_window,
    _seconds_until_next_premarket_refresh,
)


class RecommendationRefreshLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lock_path = (
            Path(__file__).resolve().parents[1]
            / f"recommendation-refresh-{uuid4().hex}.lock"
        )

    def tearDown(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def test_refresh_wait_targets_next_shanghai_0800(self) -> None:
        now = datetime(2026, 9, 2, 8, 42, 30, tzinfo=timezone.utc)

        self.assertEqual(
            _seconds_until_next_premarket_refresh(now),
            15 * 60 * 60 + 17.5 * 60,
        )

    def test_worker_restart_only_catches_up_before_market_open(self) -> None:
        self.assertTrue(
            _inside_premarket_catch_up_window(
                datetime.fromisoformat("2026-09-03T08:15:00+08:00")
            )
        )
        self.assertFalse(
            _inside_premarket_catch_up_window(
                datetime.fromisoformat("2026-09-03T09:30:00+08:00")
            )
        )

    def test_target_day_finalization_starts_at_0800(self) -> None:
        response = RecommendationIntelligenceResponse(
            refresh_id="draft",
            refreshed_at=datetime.fromisoformat("2026-09-02T16:00:00+08:00"),
            interval_minutes=1440,
            stage="draft",
            status="complete",
            relay_base_date=datetime.fromisoformat(
                "2026-09-02T16:00:00+08:00"
            ).date(),
            target_trade_date=datetime.fromisoformat(
                "2026-09-03T08:00:00+08:00"
            ).date(),
        )
        shanghai = ZoneInfo("Asia/Shanghai")

        self.assertFalse(
            should_finalize_recommendation_intelligence(
                response,
                now=datetime(2026, 9, 3, 7, 59, 59, tzinfo=shanghai),
            )
        )
        self.assertTrue(
            should_finalize_recommendation_intelligence(
                response,
                now=datetime(2026, 9, 3, 8, 0, tzinfo=shanghai),
            )
        )

    def test_dead_recent_pid_does_not_block_worker_restart(self) -> None:
        self.lock_path.write_text(
            json.dumps({"pid": 2_147_483_647}),
            encoding="utf-8",
        )

        with RefreshLoopLock(
            self.lock_path,
            stale_after=timedelta(minutes=90),
        ):
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], os.getpid())

        self.assertFalse(self.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
