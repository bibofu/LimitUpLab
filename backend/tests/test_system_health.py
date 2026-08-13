import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.system_health import expected_local_data_date


class SystemHealthTest(unittest.TestCase):
    def test_after_close_expects_today_on_weekday(self) -> None:
        expected_date, reason = expected_local_data_date(
            datetime(2026, 8, 12, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        )

        self.assertEqual(expected_date.isoformat(), "2026-08-12")
        self.assertIn("after close", reason)

    def test_before_close_expects_previous_weekday(self) -> None:
        expected_date, reason = expected_local_data_date(
            datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        )

        self.assertEqual(expected_date.isoformat(), "2026-08-11")
        self.assertIn("Before close", reason)

    def test_weekend_expects_previous_friday(self) -> None:
        expected_date, reason = expected_local_data_date(
            datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        )

        self.assertEqual(expected_date.isoformat(), "2026-08-14")
        self.assertIn("Weekend", reason)


if __name__ == "__main__":
    unittest.main()
