import unittest
from datetime import date, time

from app.collectors import HithinkLimitUpFact, HithinkLimitUpPoolSnapshot
from app.models import LimitUpEvent
from app.services.limit_up_reason import (
    count_reason_peers,
    limit_reason_tokens,
    merge_limit_up_reasons,
)


class LimitUpReasonTest(unittest.TestCase):
    def _event(self, symbol: str, concept: str = "") -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=f"股票{symbol}",
            trade_date=date(2026, 8, 31),
            first_limit_time=time(9, 40),
            last_limit_time=time(9, 40),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=100_000_000,
            turnover_rate=5,
            industry="房地产",
            concept=concept,
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def test_merge_normalizes_provider_reason_and_preserves_unmatched_event(self) -> None:
        events = [self._event("000011"), self._event("000012", "原题材")]
        snapshot = HithinkLimitUpPoolSnapshot(
            trade_date=date(2026, 8, 31),
            page=1,
            page_size=200,
            total=1,
            items=[
                HithinkLimitUpFact(
                    symbol="000011",
                    thscode="000011.SZ",
                    name="深物业A",
                    is_st=False,
                    is_new=False,
                    last_price=10,
                    change_pct=10,
                    limit_up_time="09:30:00",
                    limit_up_reason="房地产开发＋物业管理+房地产开发",
                    board_height=2,
                    board_height_text="2连板",
                    seal_amount=None,
                    max_seal_amount=None,
                )
            ],
        )

        merged, count = merge_limit_up_reasons(events, snapshot)

        self.assertEqual(count, 1)
        self.assertEqual(merged[0].concept, "房地产开发+物业管理")
        self.assertEqual(merged[1].concept, "原题材")

    def test_peer_count_uses_label_overlap_and_ignores_empty_reasons(self) -> None:
        events = [
            self._event("000011", "房地产开发+物业管理"),
            self._event("000012", "房地产开发+深圳国资"),
            self._event("000013", "健康食品"),
            self._event("000014", ""),
        ]

        self.assertEqual(limit_reason_tokens(events[0].concept), {"房地产开发", "物业管理"})
        self.assertEqual(count_reason_peers(events[0], events), 2)
        self.assertEqual(count_reason_peers(events[3], events), 0)


if __name__ == "__main__":
    unittest.main()
