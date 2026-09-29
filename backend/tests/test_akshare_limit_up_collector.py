import unittest
import os
from datetime import date, time
from unittest.mock import MagicMock, patch

from app.collectors.akshare_limit_up_collector import (
    _collect_closed_limit_up_events,
    _collect_failed_limit_up_events,
    _fetch_pool_frame,
    _load_pool_frame,
    _parse_hhmmss,
    collect_limit_up_events,
    parse_akshare_trade_date,
)
from app.models import LimitUpEvent


class AKShareLimitUpCollectorTest(unittest.TestCase):
    # Regression scenario: parse akshare trade date.
    def test_parse_akshare_trade_date(self) -> None:
        self.assertEqual(parse_akshare_trade_date("20260515"), date(2026, 5, 15))

    # Regression scenario: parse akshare trade date rejects invalid format.
    def test_parse_akshare_trade_date_rejects_invalid_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_akshare_trade_date("2026-05-15")

    # Regression scenario: parse hhmmss.
    def test_parse_hhmmss(self) -> None:
        self.assertEqual(_parse_hhmmss("092500"), time(9, 25))
        self.assertEqual(_parse_hhmmss(93046), time(9, 30, 46))

    # Regression scenario: failed pool stat is not used as consecutive board height.
    def test_failed_pool_stat_is_not_used_as_consecutive_board_height(self) -> None:
        frame = MagicMock()
        frame.iterrows.return_value = [
            (
                0,
                {
                    "代码": "002172",
                    "名称": "澳洋健康",
                    "首次封板时间": "093406",
                    "炸板次数": 1,
                    "涨停统计": "10/6",
                    "成交额": 1_121_455_552,
                    "换手率": 31.19,
                    "所属行业": "医疗服务",
                },
            )
        ]

        with patch(
            "app.collectors.akshare_limit_up_collector._load_pool_frame",
            return_value=frame,
        ):
            events = _collect_failed_limit_up_events(date(2026, 8, 25), "20260825")

        self.assertEqual(events[0].board_height, 1)
        self.assertFalse(events[0].closed_limit)

    # Regression scenario: collect limit up events keeps closed pool when failed pool errors.
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
            result = collect_limit_up_events("20260807")

        self.assertEqual(result.status, "partial")
        self.assertTrue(result.data_fresh)
        self.assertEqual(result.payload, [closed_event])
        self.assertEqual(len(result.source_errors), 1)
        self.assertIn("akshare.failed_limit_pool", result.source_errors[0])

    # Regression scenario: collect limit up events distinguishes empty from source error.
    def test_collect_limit_up_events_distinguishes_empty_from_source_error(self) -> None:
        with patch(
            "app.collectors.akshare_limit_up_collector._collect_closed_limit_up_events",
            return_value=[],
        ), patch(
            "app.collectors.akshare_limit_up_collector._collect_failed_limit_up_events",
            return_value=[],
        ):
            empty = collect_limit_up_events("20260807")

        with patch(
            "app.collectors.akshare_limit_up_collector._collect_closed_limit_up_events",
            side_effect=RuntimeError("closed pool unavailable"),
        ), patch(
            "app.collectors.akshare_limit_up_collector._collect_failed_limit_up_events",
            side_effect=RuntimeError("failed pool unavailable"),
        ):
            failed = collect_limit_up_events("20260807")

        self.assertEqual(empty.status, "empty")
        self.assertTrue(empty.data_fresh)
        self.assertEqual(empty.payload, [])
        self.assertEqual(empty.source_errors, ())
        self.assertEqual(failed.status, "error")
        self.assertFalse(failed.data_fresh)
        self.assertEqual(failed.payload, [])
        self.assertEqual(len(failed.source_errors), 2)

    def test_both_pool_loaders_use_isolated_provider_calls(self) -> None:
        frame = MagicMock()
        frame.iterrows.return_value = []
        with patch(
            "app.collectors.akshare_limit_up_collector.run_in_killable_process",
            return_value=frame,
        ) as run, patch.dict(os.environ, {"LIMITUPLAB_AKSHARE_POOL_TIMEOUT_SECONDS": "12.5"}):
            _collect_closed_limit_up_events(date(2026, 9, 23), "20260923")
            _collect_failed_limit_up_events(date(2026, 9, 23), "20260923")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args, (_fetch_pool_frame, "stock_zt_pool_em", "20260923"))
        self.assertEqual(run.call_args_list[1].args, (_fetch_pool_frame, "stock_zt_pool_zbgc_em", "20260923"))
        self.assertTrue(all(call.kwargs == {"timeout_seconds": 12.5} for call in run.call_args_list))

    def test_timeout_preserves_the_other_pool_and_names_failed_source(self) -> None:
        frame = MagicMock()
        frame.iterrows.return_value = [(0, {
            "代码": "002172", "名称": "测试股票", "首次封板时间": "093406",
            "最后封板时间": "093406", "炸板次数": 1, "连板数": 1,
            "成交额": 100_000_000, "换手率": 5, "所属行业": "测试",
        })]
        for failed_first in (True, False):
            results = [TimeoutError("Provider call timed out after 60 seconds"), frame]
            if not failed_first:
                results.reverse()
            with self.subTest(failed_first=failed_first), patch(
                "app.collectors.akshare_limit_up_collector._load_pool_frame",
                side_effect=results,
            ):
                result = collect_limit_up_events("20260923")
            self.assertEqual(result.status, "partial")
            self.assertTrue(result.data_fresh)
            self.assertEqual(len(result.payload), 1)
            self.assertEqual(result.payload[0].closed_limit, not failed_first)
            source = "closed_limit_pool" if failed_first else "failed_limit_pool"
            self.assertEqual(result.source_errors, (
                f"akshare.{source}: Provider call timed out after 60 seconds",
            ))

    def test_invalid_config_is_explicit_source_failure(self) -> None:
        for timeout in ("invalid", "0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout), patch.dict(
                os.environ, {"LIMITUPLAB_AKSHARE_POOL_TIMEOUT_SECONDS": timeout},
            ):
                result = collect_limit_up_events("20260923")
            self.assertEqual(result.status, "error")
            self.assertFalse(result.data_fresh)
            self.assertEqual(result.payload, [])
            self.assertEqual(len(result.source_errors), 2)

    def test_pool_timeout_defaults_to_sixty_seconds(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.collectors.akshare_limit_up_collector.run_in_killable_process",
        ) as run:
            _load_pool_frame("stock_zt_pool_em", "20260923")
        self.assertEqual(run.call_args.kwargs, {"timeout_seconds": 60.0})


if __name__ == "__main__":
    unittest.main()

