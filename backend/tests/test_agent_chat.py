import unittest
from datetime import date, time

from app.agents.tools import AgentToolRegistry
from app.models import LimitUpEvent


class AgentChatTest(unittest.TestCase):
    # Build the LimitUpEvent fixture used by the surrounding regression scenario.
    def _make_event(
        self,
        symbol: str,
        name: str,
        industry: str,
        concept: str,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=date(2026, 8, 7),
            first_limit_time=time(10, 0),
            last_limit_time=time(10, 0),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=250_000_000,
            turnover_rate=7.5,
            industry=industry,
            concept=concept,
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )


    # Regression scenario: stock kline keeps invalid name as explicit error.
    def test_stock_kline_keeps_invalid_name_as_explicit_error(self) -> None:
        class EmptySymbolDirectory:
            # Provide controlled source data for the surrounding test without relying on a live
            # data service.
            def collect_a_share_symbol_names(self) -> dict[str, str]:
                return {}

        registry = AgentToolRegistry(
            events=[],
            hithink_collector=EmptySymbolDirectory(),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(ValueError, "Cannot resolve stock identity"):
            registry.stock_kline("不存在股票")

    def test_market_summary_exposes_unambiguous_opened_and_unsealed_metrics(self) -> None:
        closed_after_open = self._make_event("000001", "甲", "行业", "题材").model_copy(
            update={"break_count": 1}
        )
        stable = self._make_event("000002", "乙", "行业", "题材")
        unsealed = self._make_event("000003", "丙", "行业", "题材").model_copy(
            update={"break_count": 2, "closed_limit": False}
        )

        result = AgentToolRegistry(
            events=[closed_after_open, stable, unsealed]
        ).market_summary()

        self.assertEqual(result.output["intraday_opened_count"], 2)
        self.assertEqual(result.output["unsealed_count"], 1)
        self.assertNotIn("failed_count", result.output)
        self.assertNotIn("failed_limit_up_rate", result.output)


if __name__ == "__main__":
    unittest.main()
