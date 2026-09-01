import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import (
    FirstBoardDiscoveryCandidate,
    FirstBoardDiscoveryFacts,
    FirstBoardDiscoveryResponse,
    LimitUpEvent,
    ScoreBreakdownItem,
)
from app.repositories import (
    SQLiteAuctionFinalRepository,
    SQLiteFirstBoardDiscoveryRepository,
)
from app.services.first_board_discovery import LEGACY_FIRST_BOARD_DISCOVERY_VERSION
from app.services.first_board_discovery_diagnostic import (
    build_first_board_discovery_diagnostic,
)


TEST_TMP_ROOT = Path(
    os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1])
)


class FirstBoardDiscoveryDiagnosticTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.database_path = (
            TEST_TMP_ROOT / f"first-board-discovery-diagnostic-{uuid4().hex}.sqlite"
        )
        self.discovery_repository = SQLiteFirstBoardDiscoveryRepository(
            self.database_path
        )
        self.auction_repository = SQLiteAuctionFinalRepository(self.database_path)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_detects_planted_forward_signal_without_backfilling(self) -> None:
        events: list[LimitUpEvent] = []
        target_dates = [
            date(2026, 7, 1) + timedelta(days=index) for index in range(12)
        ]
        for target_date in target_dates:
            candidates = [self._candidate(target_date, index) for index in range(10)]
            self.discovery_repository.save(
                FirstBoardDiscoveryResponse(
                    data_as_of=target_date - timedelta(days=1),
                    target_trade_date=target_date,
                    universe_count=5000,
                    eligible_count=100,
                    recalled_count=10,
                    candidates=candidates,
                    generated_by=LEGACY_FIRST_BOARD_DISCOVERY_VERSION,
                    source="test",
                    snapshot_created_at=datetime.combine(
                        target_date - timedelta(days=1),
                        time(16, 30),
                        tzinfo=timezone.utc,
                    ),
                )
            )
            events.extend(
                self._event(target_date, candidate.facts.symbol)
                for candidate in candidates[:2]
            )

        report = build_first_board_discovery_diagnostic(
            events=events,
            start_date=target_dates[0],
            end_date=target_dates[-1],
            discovery_repository=self.discovery_repository,
            auction_repository=self.auction_repository,
            top_k=3,
            bootstrap_iterations=40,
        )

        self.assertEqual(report.snapshot_count, 12)
        self.assertEqual(report.outcome_ready_trade_date_count, 12)
        self.assertEqual(report.sample_size, 120)
        self.assertEqual(report.base_top_hit_count, 24)
        self.assertEqual(report.base_top_sample_size, 36)
        self.assertEqual(report.pool_hit_count, 24)
        self.assertAlmostEqual(report.base_top_hit_rate or 0, 2 / 3, places=4)
        self.assertAlmostEqual(report.pool_hit_rate or 0, 0.2, places=4)
        self.assertEqual(report.official_top_sample_size, 0)
        momentum = next(
            item for item in report.factors if item.factor_key == "momentum"
        )
        self.assertEqual(momentum.direction, "positive")
        self.assertTrue(momentum.significant_after_bonferroni)
        self.assertEqual(report.verdict_status, "signal_requires_validation")
        self.assertIn("前向", report.caveats[0])
        self.assertIn("不构成投资建议", report.disclaimer)

    @staticmethod
    def _candidate(
        target_date: date,
        index: int,
    ) -> FirstBoardDiscoveryCandidate:
        symbol = f"002{index + 1:03d}"
        momentum_score = float(15 - index)
        score = 80.0 - index * 3.0
        return FirstBoardDiscoveryCandidate(
            facts=FirstBoardDiscoveryFacts(
                symbol=symbol,
                name=f"候选{index + 1}",
                data_as_of=target_date - timedelta(days=1),
                target_trade_date=target_date,
                close=10,
                change_pct=4,
                amount=500_000_000,
                volume=20_000_000,
                intraday_range_pct=5,
                close_location=0.8,
                open_to_close_pct=3,
                kline_bar_count=65,
                return_5d_pct=5,
                return_20d_pct=8,
                distance_20d_high_pct=-2,
                volume_ratio_5d=1.8,
                volatility_20d=2,
                ma_alignment="bullish",
                pattern="low_base_breakout",
            ),
            score=score,
            rating="B" if score >= 75 else "C" if score >= 65 else "D",
            confidence=0.7,
            score_breakdown=[
                ScoreBreakdownItem(
                    name="题材强度", score=20, max_score=30, evidence=["test"]
                ),
                ScoreBreakdownItem(
                    name="新闻催化", score=5, max_score=15, evidence=["test"]
                ),
                ScoreBreakdownItem(
                    name="市场关注度", score=5, max_score=10, evidence=["test"]
                ),
                ScoreBreakdownItem(
                    name="短期动量",
                    score=momentum_score,
                    max_score=15,
                    evidence=["planted"],
                ),
                ScoreBreakdownItem(
                    name="量能扩张", score=5, max_score=10, evidence=["test"]
                ),
                ScoreBreakdownItem(
                    name="位置结构", score=8, max_score=15, evidence=["test"]
                ),
                ScoreBreakdownItem(
                    name="数据完整性", score=5, max_score=5, evidence=["test"]
                ),
            ],
        )

    @staticmethod
    def _event(target_date: date, symbol: str) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=symbol,
            trade_date=target_date,
            first_limit_time=time(10, 0),
            last_limit_time=time(14, 30),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=400_000_000,
            turnover_rate=8,
            industry="测试",
            concept="测试",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )


if __name__ == "__main__":
    unittest.main()
