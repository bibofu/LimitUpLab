import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from app.collectors.hithink_finance_collector import (
    HithinkIncomeStatementFact,
    HithinkMarketSnapshot,
    HithinkMarketSnapshotFact,
)
from app.models import StockNewsFacts, StockNewsItem
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteRecommendationIntelligenceRepository,
)
from app.routers.agents import get_recommendation_intelligence
from app.services.recommendation_intelligence import (
    _BaseCandidate,
    refresh_recommendation_intelligence,
)


class RecommendationIntelligenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"recommendation-intelligence-{uuid4().hex}.sqlite"
        )
        self.first_repo = SQLiteFirstBoardRepository(self.database_path)
        self.limit_repo = SQLiteLimitUpRepository(
            self.database_path,
            seed_if_empty=False,
        )
        self.discovery_repo = SQLiteFirstBoardDiscoveryRepository(
            self.database_path
        )
        self.snapshot_repo = SQLiteRecommendationIntelligenceRepository(
            self.database_path
        )

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_refreshes_both_strategies_and_reuses_daily_financial_cache(self) -> None:
        now = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        candidates = [
            _BaseCandidate("discovery", date(2026, 8, 31), "600640", "国脉文化", 1, 85.3),
            _BaseCandidate("relay", date(2026, 8, 31), "002712", "思美传媒", 1, 81.2),
        ]
        quote_collector = Mock(return_value=self._quotes(now))
        news_collector = Mock(side_effect=self._news)
        financial_collector = Mock(side_effect=self._financials)

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=(candidates, date(2026, 8, 31), date(2026, 8, 31), []),
        ):
            first = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=quote_collector,
                news_collector=news_collector,
                financial_collector=financial_collector,
            )
            second = refresh_recommendation_intelligence(
                now=now + timedelta(minutes=30),
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=quote_collector,
                news_collector=news_collector,
                financial_collector=financial_collector,
            )

        self.assertEqual({item.strategy for item in first.items}, {"discovery", "relay"})
        self.assertEqual(first.items[0].latest_news[0].title, "公司最新动态")
        report = first.items[0].financial_report
        self.assertIsNotNone(report)
        self.assertEqual(report.operating_income_yoy_pct, 25.0)
        self.assertEqual(report.net_profit_yoy_pct, 100.0)
        self.assertEqual(financial_collector.call_count, 2)
        self.assertEqual(news_collector.call_count, 4)
        self.assertEqual(second.interval_minutes, 30)
        self.assertEqual(
            self.snapshot_repo.get_latest().refresh_id,
            second.refresh_id,
        )
        with patch.dict(
            os.environ,
            {"LIMITUPLAB_DATABASE_PATH": str(self.database_path)},
            clear=False,
        ):
            api_response = get_recommendation_intelligence()
        self.assertEqual(api_response.refresh_id, second.refresh_id)

    @staticmethod
    def _quotes(now: datetime) -> HithinkMarketSnapshot:
        return HithinkMarketSnapshot(
            captured_at=now,
            total=2,
            items=[
                HithinkMarketSnapshotFact(
                    symbol=symbol,
                    thscode=f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
                    name=name,
                    last_price=10.5,
                    change_pct=3.2,
                    turnover=500_000_000,
                )
                for symbol, name in (
                    ("600640", "国脉文化"),
                    ("002712", "思美传媒"),
                )
            ],
        )

    @staticmethod
    def _news(symbol: str, name: str) -> StockNewsFacts:
        fetched_at = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        return StockNewsFacts(
            symbol=symbol,
            name=name,
            fetched_at=fetched_at,
            window_days=7,
            cache_status="live",
            sources=["测试资讯"],
            items=[
                StockNewsItem(
                    symbol=symbol,
                    name=name,
                    title="公司最新动态",
                    summary="结构化测试资讯",
                    published_at=fetched_at,
                    source="测试资讯",
                    url=f"https://example.com/{symbol}",
                    item_type="news",
                    relevance_score=1,
                    fetched_at=fetched_at,
                )
            ],
        )

    @staticmethod
    def _financials(thscode: str) -> list[HithinkIncomeStatementFact]:
        return [
            HithinkIncomeStatementFact(
                thscode=thscode,
                fiscal_year=2026,
                fiscal_period="Q2",
                report_date=date(2026, 8, 22),
                period_end=date(2026, 6, 30),
                operating_income=125,
                net_profit=22,
                parent_holder_net_profit=20,
                basic_eps=0.2,
            ),
            HithinkIncomeStatementFact(
                thscode=thscode,
                fiscal_year=2025,
                fiscal_period="Q2",
                report_date=date(2025, 8, 22),
                period_end=date(2025, 6, 30),
                operating_income=100,
                net_profit=12,
                parent_holder_net_profit=10,
                basic_eps=0.1,
            ),
        ]


if __name__ == "__main__":
    unittest.main()
