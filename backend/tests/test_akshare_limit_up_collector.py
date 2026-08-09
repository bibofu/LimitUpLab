import unittest
from datetime import date, time
from unittest.mock import patch

from app.collectors.akshare_limit_up_collector import (
    _parse_board_height,
    _parse_hhmmss,
    collect_limit_up_events,
    parse_akshare_trade_date,
)
from app.models import LimitUpEvent


class AKShareLimitUpCollectorTest(unittest.TestCase):
    def test_parse_akshare_trade_date(self) -> None:
        self.assertEqual(parse_akshare_trade_date("20260515"), date(2026, 5, 15))

    def test_parse_akshare_trade_date_rejects_invalid_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_akshare_trade_date("2026-05-15")

    def test_parse_hhmmss(self) -> None:
        self.assertEqual(_parse_hhmmss("092500"), time(9, 25))
        self.assertEqual(_parse_hhmmss(93046), time(9, 30, 46))

    def test_parse_board_height(self) -> None:
        self.assertEqual(_parse_board_height("5/5"), 5)
        self.assertEqual(_parse_board_height("2/1"), 1)
        self.assertEqual(_parse_board_height(""), 1)



    def test_collect_limit_up_events_keeps_closed_pool_when_failed_pool_errors(self) -> None:
        closed_event = LimitUpEvent(
            symbol="002001",
            name="测试股票",
            trade_date=date(2026, 8, 7),
            first_limit_time=time(9, 35),
            last_limit_time=time(9, 35),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=100_000_000,
            turnover_rate=5.0,
            industry="测试",
            concept="",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

        with patch(
            "app.collectors.akshare_limit_up_collector._collect_closed_limit_up_events",
            return_value=[closed_event],
        ), patch(
            "app.collectors.akshare_limit_up_collector._collect_failed_limit_up_events",
            side_effect=RuntimeError("failed pool unavailable"),
        ):
            events = collect_limit_up_events("20260807")

        self.assertEqual(events, [closed_event])
if __name__ == "__main__":
    unittest.main()

