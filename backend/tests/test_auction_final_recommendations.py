import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from app.collectors.hithink_finance_collector import (
    HithinkAuctionFact,
    HithinkAuctionSnapshot,
)
from app.repositories import (
    SQLiteAuctionFinalRepository,
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
)
from app.routers.agents import get_auction_final_recommendations
from app.services.auction_final_recommendations import (
    _BaseCandidate,
    finalize_auction_recommendations,
    score_auction_fact,
)
from scripts.run_auction_final_loop import SHANGHAI_TZ, _next_target


class AuctionFinalRecommendationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"auction-final-{uuid4().hex}.sqlite"
        )
        self.final_repo = SQLiteAuctionFinalRepository(self.database_path)
        self.first_repo = SQLiteFirstBoardRepository(self.database_path)
        self.discovery_repo = SQLiteFirstBoardDiscoveryRepository(self.database_path)
        self.limit_repo = SQLiteLimitUpRepository(
            self.database_path,
            seed_if_empty=False,
        )

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_finalizes_both_strategies_and_preserves_first_snapshot(self) -> None:
        trade_date = date(2026, 9, 1)
        bases = [
            _BaseCandidate("discovery", date(2026, 8, 31), "600001", "发现甲", "机器人", None, 1, 86),
            _BaseCandidate("discovery", date(2026, 8, 31), "600002", "发现乙", "芯片", None, 2, 82),
            _BaseCandidate("relay", date(2026, 8, 31), "000001", "接力甲", "地产", "低位启动", 1, 88),
            _BaseCandidate("relay", date(2026, 8, 31), "000002", "接力乙", "消费", "平台突破", 2, 83),
        ]
        collector = Mock(return_value=self._snapshot(trade_date))
        with patch(
            "app.services.auction_final_recommendations._load_base_candidates",
            return_value=(bases, date(2026, 8, 31), date(2026, 8, 31), []),
        ):
            first = finalize_auction_recommendations(
                trade_date=trade_date,
                now=datetime(2026, 9, 1, 1, 25, 12, tzinfo=timezone.utc),
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.final_repo,
                auction_collector=collector,
            )
            second = finalize_auction_recommendations(
                trade_date=trade_date,
                now=datetime(2026, 9, 1, 1, 26, tzinfo=timezone.utc),
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.final_repo,
                auction_collector=collector,
            )

        self.assertEqual({item.strategy for item in first.candidates}, {"discovery", "relay"})
        discovery = [item for item in first.candidates if item.strategy == "discovery"]
        self.assertEqual(discovery[0].symbol, "600002")
        self.assertEqual(discovery[0].final_rank, 1)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(collector.call_count, 1)
        with patch.dict(
            os.environ,
            {"LIMITUPLAB_DATABASE_PATH": str(self.database_path)},
            clear=False,
        ):
            api_response = get_auction_final_recommendations(trade_date)
        self.assertEqual(api_response.trade_date, trade_date)

    def test_score_penalizes_near_one_word_auction(self) -> None:
        normal = self._fact("600001", pct=3.2, volume_ratio=1.3, turnover=0.06)
        one_word = self._fact("600002", pct=9.8, volume_ratio=1.3, turnover=0.06)

        normal_score, _, _ = score_auction_fact(normal)
        one_word_score, _, risks = score_auction_fact(one_word)

        self.assertGreater(normal_score, one_word_score)
        self.assertTrue(any("一字" in item for item in risks))

    def test_scheduler_targets_exact_092510_boundary(self) -> None:
        before = datetime(2026, 8, 31, 9, 0, tzinfo=SHANGHAI_TZ)
        target = _next_target(before)
        after_deadline = datetime(2026, 8, 31, 9, 28, tzinfo=SHANGHAI_TZ)

        self.assertEqual(target.isoformat(), "2026-08-31T09:25:10+08:00")
        self.assertEqual(
            _next_target(after_deadline).isoformat(),
            "2026-09-01T09:25:10+08:00",
        )

    def _snapshot(self, trade_date: date) -> HithinkAuctionSnapshot:
        del trade_date
        return HithinkAuctionSnapshot(
            captured_at=datetime(2026, 9, 1, 1, 25, 11, tzinfo=timezone.utc),
            auction_phase="closed",
            data_status="final",
            total=4,
            items=[
                self._fact("600001", pct=-1, volume_ratio=0.2, turnover=0.005),
                self._fact("600002", pct=3, volume_ratio=1.4, turnover=0.05),
                self._fact("000001", pct=9.8, volume_ratio=1.2, turnover=0.04),
                self._fact("000002", pct=2, volume_ratio=1.1, turnover=0.04),
            ],
        )

    @staticmethod
    def _fact(
        symbol: str,
        *,
        pct: float,
        volume_ratio: float,
        turnover: float,
    ) -> HithinkAuctionFact:
        return HithinkAuctionFact(
            symbol=symbol,
            thscode=f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
            name=symbol,
            auction_price=10,
            auction_pct=pct,
            auction_volume=10_000,
            auction_amount=10_000_000,
            auction_unmatched=0,
            auction_turnover_pct=turnover,
            auction_yesterday_ratio_pct=100,
            auction_volume_ratio=volume_ratio,
            previous_close=9.8,
            open_price=10,
            last_price=10,
            float_market_cap=5_000_000_000,
        )


if __name__ == "__main__":
    unittest.main()
