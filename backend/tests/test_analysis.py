import unittest
from datetime import date

from app.services.analysis import (
    calculate_continuation,
    calculate_daily_board_promotion,
    calculate_failed_rates,
    events_for_date,
    find_stock_event,
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

    def test_find_stock_event_supports_latest_and_exact_date(self) -> None:
        repeated = SAMPLE_EVENTS[0].model_copy(
            update={"trade_date": date(2026, 5, 14), "board_height": 2}
        )
        events = [*SAMPLE_EVENTS, repeated]

        latest = find_stock_event(events, SAMPLE_EVENTS[0].symbol)
        historical = find_stock_event(
            events,
            SAMPLE_EVENTS[0].symbol,
            trade_date=date(2026, 5, 14),
        )

        self.assertEqual(latest, SAMPLE_EVENTS[0])
        self.assertEqual(historical, repeated)
        self.assertIsNone(find_stock_event(events, "999999"))

    def test_summarize_market(self) -> None:
        summary = summarize_market(SAMPLE_EVENTS)

        self.assertEqual(summary.trade_date, date(2026, 5, 15))
        self.assertEqual(summary.limit_up_count, 3)
        self.assertEqual(summary.first_board_count, 1)
        self.assertEqual(summary.continued_board_count, 2)
        self.assertEqual(summary.failed_count, 4)
        self.assertEqual(summary.failed_limit_up_rate, 0.8)
        self.assertEqual(summary.max_board_height, 4)

    def test_first_board_list(self) -> None:
        symbols = [event.symbol for event in list_first_board(SAMPLE_EVENTS)]

        self.assertEqual(symbols, ["301489"])

    def test_continued_board_list(self) -> None:
        symbols = [event.symbol for event in list_continued_board(SAMPLE_EVENTS)]

        self.assertEqual(symbols, ["600519", "002230"])

    def test_failed_events_list(self) -> None:
        events = list_failed_events(SAMPLE_EVENTS)
        symbols = [event.symbol for event in events]

        self.assertEqual(symbols, ["603083", "002050"])
        self.assertTrue(all(not event.closed_limit for event in events))

    def test_recent_limit_up_uses_trading_days(self) -> None:
        events = list_recent_limit_up(SAMPLE_EVENTS, days=2)
        trade_dates = {event.trade_date for event in events}

        self.assertEqual(trade_dates, {date(2026, 5, 15), date(2026, 5, 14)})
        self.assertEqual(len(events), 5)

    def test_continuation_stats(self) -> None:
        stats = {item.board_height: item for item in calculate_continuation(SAMPLE_EVENTS)}

        self.assertEqual(stats[1].sample_size, 7)
        self.assertEqual(stats[1].continued_count, 3)
        self.assertEqual(stats[1].probability, 0.4286)
        self.assertEqual(stats[3].probability, 1.0)

    def test_daily_board_promotion_uses_adjacent_close_cohorts(self) -> None:
        template = SAMPLE_EVENTS[2]

        def event(symbol: str, trade_date: date, height: int, closed: bool = True):
            return template.model_copy(
                update={
                    "symbol": symbol,
                    "name": f"股票{symbol}",
                    "trade_date": trade_date,
                    "board_height": height,
                    "closed_limit": closed,
                }
            )

        events = [
            event("000099", date(2026, 7, 1), 1),
            event("000001", date(2026, 8, 20), 1),
            event("000002", date(2026, 8, 20), 1),
            event("000003", date(2026, 8, 20), 2),
            event("000004", date(2026, 8, 20), 2),
            event("000005", date(2026, 8, 20), 1, closed=False),
            event("000001", date(2026, 8, 21), 2),
            event("000002", date(2026, 8, 21), 1),
            event("000003", date(2026, 8, 21), 3),
            event("000006", date(2026, 8, 21), 1),
            event("000001", date(2026, 8, 24), 3),
            event("000002", date(2026, 8, 24), 2),
            event("000003", date(2026, 8, 24), 4),
        ]

        stats = calculate_daily_board_promotion(events, days=10)

        self.assertEqual(len(stats), 2)
        first, latest = stats
        self.assertEqual(first.previous_trade_date, date(2026, 8, 20))
        self.assertEqual(first.trade_date, date(2026, 8, 21))
        self.assertEqual(first.sample_size, 4)
        self.assertEqual(first.promoted_count, 2)
        self.assertEqual(first.probability, 0.5)
        self.assertEqual(first.first_board_probability, 0.5)
        self.assertEqual(first.continued_board_probability, 0.5)
        self.assertEqual(first.buckets[0].promoted_count, 1)
        self.assertEqual(first.buckets[1].promoted_count, 1)
        self.assertEqual(
            [item.symbol for item in first.promoted_stocks],
            ["000003", "000001"],
        )
        self.assertEqual(first.promoted_stocks[0].from_board_height, 2)
        self.assertEqual(first.promoted_stocks[0].to_board_height, 3)
        self.assertEqual(first.promoted_stocks[1].name, "股票000001")
        self.assertEqual(latest.probability, 0.75)
        self.assertEqual(latest.first_board_probability, 0.5)
        self.assertEqual(latest.continued_board_probability, 1.0)
        self.assertEqual(
            [item.symbol for item in latest.promoted_stocks],
            ["000003", "000001", "000002"],
        )
        self.assertEqual(
            calculate_daily_board_promotion(events, days=1),
            [latest],
        )

    def test_failed_rate_stats(self) -> None:
        stats = {item.board_height: item for item in calculate_failed_rates(SAMPLE_EVENTS)}

        self.assertEqual(stats[1].sample_size, 7)
        self.assertEqual(stats[1].failed_count, 7)
        self.assertEqual(stats[1].failed_rate, 1.0)
        self.assertEqual(stats[4].failed_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
