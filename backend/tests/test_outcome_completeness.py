import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

from app.models import LimitUpEvent, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.first_board_features import build_first_board_outcome
from app.services.evaluation_agent import persist_agent_predictions_for_dates
from app.services.outcome_completeness import build_top10_outcome_completeness
from scripts.update_daily_data import backfill_recent_daily_top_candidate_bars


class OutcomeCompletenessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"outcome-completeness-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.trade_dates = [
            date(2026, 8, 10),
            date(2026, 8, 11),
            date(2026, 8, 12),
            date(2026, 8, 13),
            date(2026, 8, 14),
            date(2026, 8, 17),
        ]
        self.events = [
            self._event("000001" if index == 0 else f"0001{index:02d}", item)
            for index, item in enumerate(self.trade_dates)
        ]
        self.base_event = self.events[0]
        persist_agent_predictions_for_dates(
            events=self.events,
            trade_dates=[self.trade_dates[0]],
            repository=self.repository,
            top_per_day=1,
            prediction_source="live",
            data_as_of=self.trade_dates[0],
        )

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_missing_exact_d1_is_not_relabelled_from_later_bar(self) -> None:
        bars = [
            self._bar(item)
            for item in self.trade_dates
            if item != self.trade_dates[1]
        ]

        outcome = build_first_board_outcome(
            event=self.base_event,
            bars=bars,
            future_events=self.events,
            trading_dates=self.trade_dates,
        )

        self.assertFalse(outcome.next_day_ready)
        self.assertIsNone(outcome.next_trade_date)
        self.assertFalse(outcome.three_day_ready)

    def test_report_recovers_after_missing_bar_is_backfilled(self) -> None:
        incomplete_bars = [
            self._bar(item)
            for item in self.trade_dates
            if item != self.trade_dates[1]
        ]
        self.repository.upsert_daily_bars(incomplete_bars)
        self.repository.upsert_outcomes(
            [
                build_first_board_outcome(
                    event=self.base_event,
                    bars=incomplete_bars,
                    future_events=self.events,
                    trading_dates=self.trade_dates,
                )
            ]
        )

        incomplete = self._report()

        self.assertEqual(incomplete.status, "partial")
        self.assertEqual(incomplete.d1_ready_count, 0)
        self.assertEqual(incomplete.d3_ready_count, 0)
        self.assertEqual(incomplete.d5_ready_count, 0)
        self.assertEqual(incomplete.missing_case_count, 1)
        self.assertEqual(incomplete.dates[0].d1_missing_symbols, ["000001"])

        complete_bars = [self._bar(item) for item in self.trade_dates]
        self.repository.upsert_daily_bars([self._bar(self.trade_dates[1])])
        self.repository.upsert_outcomes(
            [
                build_first_board_outcome(
                    event=self.base_event,
                    bars=complete_bars,
                    future_events=self.events,
                    trading_dates=self.trade_dates,
                )
            ]
        )

        complete = self._report()

        self.assertEqual(complete.status, "healthy")
        self.assertEqual(complete.d1_ready_count, 1)
        self.assertEqual(complete.d3_ready_count, 1)
        self.assertEqual(complete.d5_ready_count, 1)
        self.assertEqual(complete.missing_case_count, 0)

    def test_daily_backfill_retries_partial_case_outside_recent_six_dates(self) -> None:
        extra_date = date(2026, 8, 18)
        events = [*self.events, self._event("000107", extra_date)]
        incomplete_bars = [self._bar(item) for item in self.trade_dates[:5]]
        self.repository.upsert_daily_bars(incomplete_bars)
        self.repository.upsert_outcomes(
            [
                build_first_board_outcome(
                    event=self.base_event,
                    bars=incomplete_bars,
                    future_events=events,
                    trading_dates=[*self.trade_dates, extra_date],
                )
            ]
        )

        result = backfill_recent_daily_top_candidate_bars(
            events=events,
            first_board_repository=self.repository,
            trading_days=6,
            top_per_day=1,
            max_kline_fetches=5,
            as_of_date=extra_date,
            bar_collector=lambda _symbol, _base, _as_of: [
                self._bar(item) for item in self.trade_dates
            ],
            spot_bar_collector=None,
        )

        self.assertEqual(result["fetch_count"], 1)
        self.assertEqual(result["five_day_paths_expected"], 1)
        self.assertEqual(result["five_day_paths_ready"], 1)
        self.assertEqual(result["outcome_completeness"]["status"], "healthy")

    def _report(self):
        return build_top10_outcome_completeness(
            events=self.events,
            repository=self.repository,
            as_of_date=self.trade_dates[-1],
            tracking_days=6,
            top_per_day=10,
        )

    def _event(self, symbol: str, trade_date: date) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=symbol,
            trade_date=trade_date,
            first_limit_time=time(9, 35),
            last_limit_time=time(9, 40),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=300_000_000,
            turnover_rate=6,
            industry="test",
            concept="test",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def _bar(self, trade_date: date) -> StockDailyBar:
        offset = self.trade_dates.index(trade_date)
        return StockDailyBar(
            symbol=self.base_event.symbol,
            trade_date=trade_date,
            open=10 + offset,
            high=10.5 + offset,
            low=9.5 + offset,
            close=10.2 + offset,
            volume=1_000_000,
            amount=10_000_000,
            change_pct=1,
            source="test",
            created_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
