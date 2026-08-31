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
from app.models import StockKLineBar
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
)
from app.agents.tools import AgentToolRegistry
from app.routers.agents import get_first_board_discovery
from app.services.first_board_discovery import (
    FIRST_BOARD_DISCOVERY_VERSION,
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
            total=4,
            items=[
                self._quote("000001", "强势样本", amount=900_000_000, change=4.5),
                self._quote("000002", "低流动性", amount=30_000_000, change=3.0),
                self._quote("000003", "ST风险", amount=600_000_000, change=3.0),
                self._quote("000004", "已涨停", amount=800_000_000, change=10.0),
            ],
        )

        response = refresh_first_board_discovery(
            target_trade_date=date(2026, 9, 1),
            market_collector=lambda: snapshot,
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

        self.assertEqual(response.universe_count, 4)
        self.assertEqual(response.eligible_count, 1)
        self.assertEqual(response.recalled_count, 1)
        self.assertEqual(len(response.candidates), 1)
        self.assertEqual(response.candidates[0].facts.symbol, "000001")
        self.assertLess(response.candidates[0].facts.volume_ratio_5d or 0, 5)
        self.assertEqual(response.generated_by, FIRST_BOARD_DISCOVERY_VERSION)
        self.assertAlmostEqual(
            sum(item.max_score for item in response.candidates[0].score_breakdown),
            100,
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
                high=9.2 + index * 0.01,
                low=8.9 + index * 0.01,
                close=9.1 + index * 0.01,
                volume=8_000_000 + index * 100_000,
            )
            for index in range(days)
        ]


if __name__ == "__main__":
    unittest.main()
