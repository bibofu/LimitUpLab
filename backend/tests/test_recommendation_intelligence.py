import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from app.models import (
    RecommendationIntelligenceItem,
    RecommendationIntelligenceResponse,
    StockNewsFacts,
)
from app.repositories import (
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteRecommendationIntelligenceRepository,
)
from app.services.recommendation_intelligence import (
    _BaseCandidate,
    refresh_recommendation_intelligence,
)


class RecommendationIntelligenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).resolve().parents[1] / f"relay-{uuid4().hex}.sqlite"
        self.first_repo = SQLiteFirstBoardRepository(self.database_path)
        self.limit_repo = SQLiteLimitUpRepository(self.database_path, seed_if_empty=False)
        self.snapshot_repo = SQLiteRecommendationIntelligenceRepository(self.database_path)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_refresh_is_relay_only(self) -> None:
        now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay", date(2026, 8, 31), "002712", "思美传媒",
            "文化传媒", "低位启动", 1, 81.2,
        )
        empty_news = StockNewsFacts(
            symbol=candidate.symbol,
            name=candidate.name,
            fetched_at=now,
            window_days=7,
            cache_status="fresh",
        )
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=lambda _symbols: SimpleNamespace(items=[], captured_at=now),
                news_collector=lambda _symbol, _name: empty_news,
                financial_collector=lambda _symbol: [],
                dragon_tiger_collector=lambda _date: {},
                popularity_collector=lambda: {},
            )

        self.assertEqual(response.relay_pool_size, 1)
        self.assertEqual([item.strategy for item in response.items], ["relay"])
        self.assertEqual(response.relay_base_date, date(2026, 8, 31))
        self.assertFalse(hasattr(response, "discovery_base_date"))

    def test_discovery_item_is_rejected_by_public_model(self) -> None:
        with self.assertRaises(ValidationError):
            RecommendationIntelligenceItem(
                strategy="discovery",
                base_trade_date=date(2026, 8, 31),
                symbol="600640",
                name="国脉文化",
                rank=1,
                base_score=80,
                refreshed_at=datetime.now(timezone.utc),
            )

    def test_latest_displayable_falls_back_when_missed_cutoff_has_no_items(self) -> None:
        snapshot_at = datetime(2026, 9, 7, 16, tzinfo=timezone.utc)
        candidate = RecommendationIntelligenceItem(
            base_trade_date=date(2026, 9, 7),
            symbol="002712",
            name="思美传媒",
            rank=1,
            base_score=81.2,
            refreshed_at=snapshot_at,
        )
        available = RecommendationIntelligenceResponse(
            refresh_id="available",
            refreshed_at=snapshot_at,
            interval_minutes=30,
            target_trade_date=date(2026, 9, 8),
            relay_base_date=date(2026, 9, 7),
            status="complete",
            items=[candidate],
        )
        missed = RecommendationIntelligenceResponse(
            refresh_id="missed",
            refreshed_at=datetime(2026, 9, 9, 2, tzinfo=timezone.utc),
            interval_minutes=30,
            stage="missed_cutoff",
            target_trade_date=date(2026, 9, 9),
            relay_base_date=date(2026, 9, 8),
            status="partial",
            warnings=["开盘前未生成快照"],
        )

        self.snapshot_repo.save(available)
        self.snapshot_repo.save(missed)

        self.assertEqual(self.snapshot_repo.get_latest(), missed)
        self.assertEqual(
            self.snapshot_repo.get_latest_displayable(),
            available,
        )


if __name__ == "__main__":
    unittest.main()
