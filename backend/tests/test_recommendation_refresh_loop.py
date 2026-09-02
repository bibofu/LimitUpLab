import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from scripts.run_recommendation_refresh_loop import (
    RefreshLoopLock,
    _seconds_until_next_slot,
)


class RecommendationRefreshLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lock_path = (
            Path(__file__).resolve().parents[1]
            / f"recommendation-refresh-{uuid4().hex}.lock"
        )

    def tearDown(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def test_refresh_wait_is_aligned_to_half_hour_wall_clock(self) -> None:
        now = datetime(2026, 9, 2, 8, 42, 30, tzinfo=timezone.utc)

        self.assertEqual(_seconds_until_next_slot(30, now), 17.5 * 60)

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
