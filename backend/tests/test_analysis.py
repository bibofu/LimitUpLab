import unittest
from datetime import date

from app.services.analysis import (
    calculate_continuation,
    calculate_failed_rates,
    events_for_date,
    latest_trade_date,
    list_continued_board,
    list_failed_events,
    list_first_board,
    list_recent_limit_up,
    summarize_market,
)
from app.services.sample_data import SAMPLE_EVENTS


class AnalysisTest(unittest.TestCase):
    def test_latest_trade_date(self) -> None:
        self.assertEqual(latest_trade_date(SAMPLE_EVENTS), date(2026, 5, 15))

    def test_events_for_latest_date(self) -> None:
        events = events_for_date(SAMPLE_EVENTS)

        self.assertEqual(len(events), 5)
        self.assertTrue(all(event.trade_date == date(2026, 5, 15) for event in events))

    def test_summarize_market(self) -> None:
        summary = summarize_market(SAMPLE_EVENTS)

        self.assertEqual(summary.trade_date, date(2026, 5, 15))
        self.assertEqual(summary.limit_up_count, 5)
        self.assertEqual(summary.first_board_count, 3)
        self.assertEqual(summary.continued_board_count, 2)
        self.assertEqual(summary.failed_count, 4)
        self.assertEqual(summary.failed_limit_up_rate, 0.8)
        self.assertEqual(summary.max_board_height, 4)
        self.assertEqual(summary.sentiment, "cooling")

    def test_first_board_list(self) -> None:
        symbols = [event.symbol for event in list_first_board(SAMPLE_EVENTS)]

        self.assertEqual(symbols, ["301489", "603083", "002050"])

    def test_continued_board_list(self) -> None:
        symbols = [event.symbol for event in list_continued_board(SAMPLE_EVENTS)]

        self.assertEqual(symbols, ["600519", "002230"])

    def test_failed_events_list(self) -> None:
        symbols = [event.symbol for event in list_failed_events(SAMPLE_EVENTS)]

        self.assertEqual(symbols, ["603083", "002050", "301489", "002230"])

    def test_recent_limit_up_uses_trading_days(self) -> None:
        events = list_recent_limit_up(SAMPLE_EVENTS, days=2)
        trade_dates = {event.trade_date for event in events}

        self.assertEqual(trade_dates, {date(2026, 5, 15), date(2026, 5, 14)})
        self.assertEqual(len(events), 8)

    def test_continuation_stats(self) -> None:
        stats = {item.board_height: item for item in calculate_continuation(SAMPLE_EVENTS)}

        self.assertEqual(stats[1].sample_size, 7)
        self.assertEqual(stats[1].continued_count, 3)
        self.assertEqual(stats[1].probability, 0.4286)
        self.assertEqual(stats[3].probability, 1.0)

    def test_failed_rate_stats(self) -> None:
        stats = {item.board_height: item for item in calculate_failed_rates(SAMPLE_EVENTS)}

        self.assertEqual(stats[1].sample_size, 7)
        self.assertEqual(stats[1].failed_count, 7)
        self.assertEqual(stats[1].failed_rate, 1.0)
        self.assertEqual(stats[4].failed_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
