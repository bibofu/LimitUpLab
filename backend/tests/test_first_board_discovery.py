import unittest
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from app.collectors.hithink_finance_collector import (
    HithinkMarketSnapshot,
    HithinkMarketSnapshotFact,
)
from app.models import FirstBoardDiscoveryTheme, StockKLineBar
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
)
from app.agents.tools import AgentToolRegistry
from app.routers.agents import get_first_board_discovery
from app.services.first_board_discovery import (
    FIRST_BOARD_DISCOVERY_VERSION,
    FirstBoardDiscoveryContext,
    _theme_matches_news,
    refresh_first_board_discovery,
)


class FirstBoardDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"first-board-discovery-{uuid4().hex}.sqlite"
        )
        self.first_board_repository = SQLiteFirstBoardRepository(self.database_path)
        self.snapshot_repository = SQLiteFirstBoardDiscoveryRepository(
            self.database_path
        )

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_refresh_filters_universe_scores_and_persists_top_candidates(self) -> None:
        data_as_of = date(2026, 8, 31)
        snapshot = HithinkMarketSnapshot(
            captured_at=datetime(2026, 8, 31, 7, tzinfo=timezone.utc),
            total=5,
            items=[
                self._quote("000001", "强势样本", amount=900_000_000, change=4.5),
                self._quote("000002", "低流动性", amount=30_000_000, change=3.0),
                self._quote("000003", "ST风险", amount=600_000_000, change=3.0),
                self._quote("000004", "已涨停", amount=800_000_000, change=10.0),
                self._quote("000005", "非热门题材", amount=1_200_000_000, change=7.0),
            ],
        )
        theme = FirstBoardDiscoveryTheme(
            name="AI视频",
            category="concept",
            change_pct=5.7,
            rank=1,
            member_count=20,
            news_headlines=["AI视频应用迎来新进展"],
        )
        context = FirstBoardDiscoveryContext(
            themes=[theme],
            memberships={"000001": [theme]},
            popularity_ranks={"000001": 8},
            warnings=[],
        )

        response = refresh_first_board_discovery(
            target_trade_date=date(2026, 9, 1),
            market_collector=lambda: snapshot,
            theme_collector=lambda _: context,
            history_collector=lambda symbol, days, end_date: self._history(
                symbol,
                end_date or data_as_of,
                days,
            ),
            first_board_repository=self.first_board_repository,
            snapshot_repository=self.snapshot_repository,
            recall_limit=10,
            top_k=10,
            max_workers=1,
        )

        self.assertEqual(response.universe_count, 5)
        self.assertEqual(response.eligible_count, 1)
        self.assertEqual(response.recalled_count, 1)
        self.assertEqual(len(response.candidates), 1)
        self.assertEqual(response.candidates[0].facts.symbol, "000001")
        self.assertEqual(response.candidates[0].facts.themes[0].name, "AI视频")
        self.assertEqual(response.candidates[0].facts.popularity_rank, 8)
        self.assertIsNotNone(response.candidates[0].facts.return_60d_pct)
        self.assertIsNotNone(response.candidates[0].facts.position_60d_pct)
        self.assertLessEqual(response.candidates[0].facts.position_60d_pct or 0, 85)
        self.assertEqual(
            response.candidates[0].facts.news_catalysts,
            ["AI视频应用迎来新进展"],
        )
        self.assertEqual(response.themes[0].name, "AI视频")
        self.assertLess(response.candidates[0].facts.volume_ratio_5d or 0, 5)
        self.assertEqual(response.generated_by, FIRST_BOARD_DISCOVERY_VERSION)
        self.assertIn("不构成投资建议", response.disclaimer)
        self.assertAlmostEqual(
            sum(item.max_score for item in response.candidates[0].score_breakdown),
            100,
        )
        self.assertEqual(
            [item.name for item in response.candidates[0].score_breakdown],
            [
                "题材热度",
                "新闻催化",
                "市场关注度",
                "低位结构",
                "启动信号",
                "数据完整性",
            ],
        )
        persisted = self.snapshot_repository.get_latest()
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.model_dump(), response.model_dump())
        self.assertGreaterEqual(
            len(self.first_board_repository.list_daily_bars("000001")),
            60,
        )

        tool_result = AgentToolRegistry(
            events=[],
            first_board_repository=self.first_board_repository,
        ).first_board_discovery()
        self.assertEqual(tool_result.name, "first_board_discovery")
        self.assertEqual(tool_result.output.data_as_of, data_as_of)
        self.assertEqual(tool_result.trace_output["candidates"][0]["symbol"], "000001")

        with patch.dict(
            os.environ,
            {"LIMITUPLAB_DATABASE_PATH": str(self.database_path)},
            clear=False,
        ):
            api_response = get_first_board_discovery(data_as_of=data_as_of)
        self.assertEqual(api_response.candidates[0].facts.name, "强势样本")

    def test_refresh_excludes_candidates_already_near_the_60_day_high(self) -> None:
        data_as_of = date(2026, 8, 31)
        theme = FirstBoardDiscoveryTheme(
            name="机器人",
            category="concept",
            change_pct=4.2,
            rank=1,
            news_headlines=["机器人产业链迎来新订单"],
        )
        snapshot = HithinkMarketSnapshot(
            captured_at=datetime(2026, 8, 31, 7, tzinfo=timezone.utc),
            total=1,
            items=[self._quote("000010", "高位样本", amount=800_000_000, change=4.0)],
        )
        context = FirstBoardDiscoveryContext(
            themes=[theme],
            memberships={"000010": [theme]},
            popularity_ranks={"000010": 12},
            warnings=[],
        )

        response = refresh_first_board_discovery(
            target_trade_date=date(2026, 9, 1),
            market_collector=lambda: snapshot,
            theme_collector=lambda _: context,
            history_collector=lambda symbol, days, end_date: self._high_position_history(
                symbol,
                end_date or data_as_of,
                days,
            ),
            first_board_repository=self.first_board_repository,
            snapshot_repository=self.snapshot_repository,
            recall_limit=10,
            top_k=10,
            max_workers=1,
        )

        self.assertEqual(response.recalled_count, 1)
        self.assertEqual(response.candidates, [])
        self.assertTrue(any("位置偏高" in warning for warning in response.warnings))

    def test_ai_alias_requires_a_real_token_or_chinese_term(self) -> None:
        self.assertFalse(
            _theme_matches_news("AI视频", "The chairman discussed quarterly results")
        )
        self.assertTrue(_theme_matches_news("AI视频", "AI 视频生成模型发布"))
        self.assertTrue(_theme_matches_news("AI视频", "人工智能视频应用取得进展"))

    @staticmethod
    def _quote(
        symbol: str,
        name: str,
        *,
        amount: float,
        change: float,
    ) -> HithinkMarketSnapshotFact:
        return HithinkMarketSnapshotFact(
            symbol=symbol,
            thscode=f"{symbol}.SZ",
            name=name,
            last_price=10.45,
            change_pct=change,
            turnover=amount,
            volume=2_000_000_000,
            open_price=10.0,
            high_price=10.6,
            low_price=9.9,
            previous_close=10.0,
        )

    @staticmethod
    def _history(symbol: str, end_date: date, days: int) -> list[StockKLineBar]:
        del symbol
        started = end_date - timedelta(days=days - 1)
        return [
            StockKLineBar(
                trade_date=started + timedelta(days=index),
                open=9 + index * 0.01,
                high=11.0,
                low=8.9 + index * 0.01,
                close=9.1 + index * 0.01,
                volume=8_000_000 + index * 100_000,
            )
            for index in range(days)
        ]

    @staticmethod
    def _high_position_history(
        symbol: str,
        end_date: date,
        days: int,
    ) -> list[StockKLineBar]:
        del symbol
        started = end_date - timedelta(days=days - 1)
        return [
            StockKLineBar(
                trade_date=started + timedelta(days=index),
                open=9 + index * 0.01,
                high=9.2 + index * 0.01,
                low=8.9 + index * 0.01,
                close=9.1 + index * 0.01,
                volume=8_000_000 + index * 100_000,
            )
            for index in range(days)
        ]


if __name__ == "__main__":
    unittest.main()
