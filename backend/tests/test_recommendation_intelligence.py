import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.collectors.first_board_enrichment_collector import DragonTigerFact, PopularityFact
from app.models import (
    RecommendationFinancialReport,
    RecommendationIntelligenceItem,
    RecommendationIntelligenceResponse,
    StockNewsFacts,
    StockNewsItem,
)
from app.repositories import (
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteRecommendationIntelligenceRepository,
)
from app.services.recommendation_intelligence import (
    _BaseCandidate,
    _CandidateEvidence,
    _CandidateRefreshContext,
    _build_candidate_item,
    refresh_recommendation_intelligence,
)


class RecommendationIntelligenceTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.database_path = Path(__file__).resolve().parents[1] / f"relay-{uuid4().hex}.sqlite"
        self.first_repo = SQLiteFirstBoardRepository(self.database_path)
        self.limit_repo = SQLiteLimitUpRepository(self.database_path, seed_if_empty=False)
        self.snapshot_repo = SQLiteRecommendationIntelligenceRepository(self.database_path)

    # Release the test resources and restore the environment after this test scope.
    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    # Regression scenario: refresh is relay only.
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
        ), patch(
            "app.services.recommendation_intelligence.collect_a_share_trade_dates",
            return_value=[date(2026, 8, 31), date(2026, 9, 1)],
        ):
            # The inline callback supplies the fixture value or replacement behavior used by this
            # test; it is evaluated only when the code under test calls it.
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

    # Regression scenario: existing close draft is not refreshed before target day 0800.
    def test_existing_close_draft_is_not_refreshed_before_target_day_0800(self) -> None:
        base_date = date(2026, 8, 31)
        target_date = date(2026, 9, 1)
        close_time = datetime.fromisoformat("2026-08-31T16:00:00+08:00")
        candidate = _BaseCandidate(
            "relay", base_date, "002712", "思美传媒",
            "文化传媒", "低位启动", 1, 81.2,
        )
        previous = RecommendationIntelligenceResponse(
            refresh_id="close-draft",
            refreshed_at=close_time,
            interval_minutes=1440,
            stage="draft",
            status="complete",
            relay_base_date=base_date,
            target_trade_date=target_date,
        )
        self.snapshot_repo.save(previous)

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], base_date, []),
        ), patch(
            "app.services.recommendation_intelligence.collect_a_share_trade_dates",
            return_value=[base_date, target_date],
        ):
            # The inline callback supplies the fixture value or replacement behavior used by this
            # test; it is evaluated only when the code under test calls it.
            response = refresh_recommendation_intelligence(
                now=datetime.fromisoformat("2026-08-31T22:00:00+08:00"),
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=lambda _symbols: self.fail(
                    "intermediate quote refresh must not run"
                ),
            )

        self.assertEqual(response.refresh_id, "close-draft")
        self.assertEqual(response.refreshed_at, close_time)

    def test_refresh_keeps_base_ranks_when_post_close_news_changes_draft_order(self) -> None:
        base_date = date(2026, 8, 31)
        now = datetime.fromisoformat("2026-09-01T08:05:00+08:00")
        candidates = [
            _BaseCandidate("relay", base_date, symbol, symbol, "行业", None, rank, 80)
            for rank, symbol in enumerate(("600001", "600002", "600003"), start=1)
        ]
        publications = {
            "600001": ["2026-08-31T14:00:00+08:00"],
            "600002": ["2026-08-31T16:00:00+08:00"],
            "600003": ["2026-08-31T16:00:00+08:00", "2026-08-31T17:00:00+08:00"],
        }
        news = {
            symbol: StockNewsFacts(
                symbol=symbol, name=symbol, fetched_at=now,
                window_days=7, cache_status="fresh", items=[
                    StockNewsItem(
                        symbol=symbol, name=symbol, title=f"中标项目{index}",
                        summary="", published_at=datetime.fromisoformat(published),
                        source="fixture", url=f"https://example.test/{symbol}/{index}",
                        item_type="announcement", relevance_score=1, fetched_at=now,
                    )
                    for index, published in enumerate(timestamps)
                ],
            )
            for symbol, timestamps in publications.items()
        }
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=(list(reversed(candidates)), base_date, []),
        ), patch(
            "app.services.recommendation_intelligence.collect_a_share_trade_dates",
            return_value=[base_date, now.date()],
        ):
            response = refresh_recommendation_intelligence(
                now=now, max_workers=1, limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo, snapshot_repository=self.snapshot_repo,
                quote_collector=lambda _symbols: SimpleNamespace(items=[], captured_at=now),
                news_collector=lambda symbol, _name: news[symbol],
                financial_collector=lambda _symbol: [],
                dragon_tiger_collector=lambda _date: {}, popularity_collector=lambda: {},
            )

        self.assertEqual([item.symbol for item in response.items], ["600003", "600001", "600002"])
        self.assertEqual([
            (item.rule_rank, item.base_rank, item.rank, item.rule_score, item.base_score,
             item.draft_score, item.close_information_adjustment, item.dynamic_adjustment)
            for item in response.items
        ], [
            (3, 3, 1, 80, 80, 86, 0, 6),
            (1, 1, 2, 80, 83, 83, 3, 0),
            (2, 2, 3, 80, 80, 83, 0, 3),
        ])
        self.assertEqual(self.snapshot_repo.get_latest(), response)
        self.assertEqual([candidate.rank for candidate in candidates], [1, 2, 3])

    def test_refresh_preserves_stale_market_facts_without_scoring_failed_sources(self) -> None:
        base_date = date(2026, 8, 31)
        captured_at = datetime.fromisoformat("2026-08-31T16:00:00+08:00")
        now = datetime.fromisoformat("2026-09-01T08:05:00+08:00")
        candidate = _BaseCandidate(
            "relay", base_date, "600001", "样本", "行业", None, 1, 80,
            amount=100_000_000, popularity_baseline_ready=True, popularity_rank=80,
            popularity_snapshot_at=captured_at,
        )
        previous_item = RecommendationIntelligenceItem(
            base_trade_date=base_date, symbol=candidate.symbol, name=candidate.name,
            rank=1, base_score=80, current_price=12.5, change_pct=2.5, turnover=100_000_000,
            quote_captured_at=captured_at, refreshed_at=captured_at,
            dragon_tiger_on_list=True, dragon_tiger_net_buy_amount=6_000_000,
            dragon_tiger_source="stored-dragon-tiger", popularity_rank=10,
            popularity_rank_change=70, popularity_snapshot_at=captured_at,
            popularity_source="stored-popularity",
        )
        previous = RecommendationIntelligenceResponse(
            refresh_id="previous", refreshed_at=captured_at, interval_minutes=1440,
            relay_base_date=base_date, target_trade_date=now.date(), status="complete",
            items=[previous_item],
        )
        self.snapshot_repo.save(previous)
        failed_collector = Mock(side_effect=RuntimeError("offline"))
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], base_date, []),
        ), patch(
            "app.services.recommendation_intelligence.collect_a_share_trade_dates",
            return_value=[base_date, now.date()],
        ):
            response = refresh_recommendation_intelligence(
                now=now, max_workers=1, limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo, snapshot_repository=self.snapshot_repo,
                quote_collector=failed_collector, news_collector=failed_collector,
                financial_collector=lambda _symbol: [],
                dragon_tiger_collector=failed_collector, popularity_collector=failed_collector,
            )

        item = response.items[0]
        self.assertEqual(response.status, "partial")
        self.assertEqual((item.current_price, item.change_pct, item.turnover), (12.5, 2.5, 100_000_000))
        self.assertEqual(item.quote_captured_at, captured_at)
        self.assertEqual((item.popularity_rank, item.popularity_snapshot_at), (10, captured_at))
        self.assertEqual(item.popularity_source, "stored-popularity")
        self.assertEqual(item.dragon_tiger_net_buy_amount, 6_000_000)
        self.assertEqual(item.dragon_tiger_source, "stored-dragon-tiger")
        self.assertEqual((item.dragon_tiger_adjustment, item.popularity_adjustment, item.draft_score), (0, 0, 80))
        self.assertEqual(item.data_missing, [
            "新闻刷新失败：offline", "财报源未返回季度利润表", "最新行情不可用", "最近 7 日无直接相关新闻",
            "最新季度财报不可用", "最新龙虎榜刷新不可用", "最新人气榜刷新不可用",
        ])
        self.assertIn("1 只股票的新闻或财报刷新发生错误，已保留可用缓存。", response.warnings)

    def test_dynamic_cap_keeps_component_scores_and_their_explanations(self) -> None:
        base_date = date(2026, 8, 31)
        cutoff = datetime.fromisoformat("2026-08-31T15:00:00+08:00")
        now = datetime.fromisoformat("2026-09-01T08:05:00+08:00")
        candidate = _BaseCandidate(
            "relay", base_date, "600001", "样本", "行业", None, 1, 80,
            amount=100_000_000, popularity_baseline_ready=True,
            popularity_rank=100, popularity_snapshot_at=cutoff,
        )
        news = StockNewsFacts(
            symbol=candidate.symbol, name=candidate.name, fetched_at=now,
            window_days=7, cache_status="fresh", items=[StockNewsItem(
                symbol=candidate.symbol, name=candidate.name, title="中标新项目",
                summary="", published_at=now, fetched_at=now, source="fixture",
                url="https://example.test/news", item_type="announcement", relevance_score=1,
            )],
        )
        report = RecommendationFinancialReport(
            fiscal_year=2026, fiscal_period="H1", report_date=now.date(),
            period_end=date(2026, 6, 30), net_profit_yoy_pct=60, fetched_at=now,
        )
        context = _CandidateRefreshContext(
            refreshed_at=now, quote_by_symbol={}, quote_captured_at=None,
            dragon_tiger_by_symbol={candidate.symbol: DragonTigerFact(
                candidate.symbol, None, None, 6_000_000, None, None, "fixture",
            )}, dragon_tiger_ready=True,
            popularity_by_symbol={candidate.symbol: PopularityFact(candidate.symbol, 10, None, now)},
            popularity_captured_at=now, popularity_ready=True,
        )

        item = _build_candidate_item(candidate, _CandidateEvidence(news, report, []), None, context)

        self.assertEqual((item.news_adjustment, item.financial_adjustment,
                          item.dragon_tiger_adjustment, item.popularity_adjustment), (3, 3, 2, 2))
        self.assertEqual((item.rule_score, item.base_score, item.dynamic_adjustment, item.draft_score),
                         (80, 80, 6, 86))
        self.assertEqual(item.facts_cutoff_at, cutoff)
        self.assertEqual(item.popularity_rank_change, 90)
        self.assertEqual(item.close_information_reasons, [])
        self.assertEqual(len(item.update_reasons), 5)
        self.assertEqual(item.update_reasons[-1], "盘后动态修正受 ±6 分约束，原始合计 +10 分")

    def test_dragon_tiger_hot_rank_precedes_stale_popularity_without_scoring_failed_feed(self) -> None:
        base_date = date(2026, 8, 31)
        previous_at = datetime.fromisoformat("2026-08-31T16:00:00+08:00")
        now = datetime.fromisoformat("2026-09-01T08:05:00+08:00")
        candidate = _BaseCandidate(
            "relay", base_date, "600001", "样本", "行业", None, 1, 80,
            popularity_baseline_ready=True, popularity_rank=100, popularity_snapshot_at=previous_at,
        )
        previous = RecommendationIntelligenceItem(
            base_trade_date=base_date, symbol=candidate.symbol, name=candidate.name,
            rank=1, base_score=80, refreshed_at=previous_at, popularity_rank=12,
            popularity_snapshot_at=previous_at, popularity_source="previous-popularity",
        )
        context = _CandidateRefreshContext(
            refreshed_at=now, quote_by_symbol={}, quote_captured_at=None,
            dragon_tiger_by_symbol={candidate.symbol: DragonTigerFact(
                candidate.symbol, None, None, 0, None, None, "new-dragon-tiger", hot_rank=8,
            )}, dragon_tiger_ready=True, popularity_by_symbol={},
            popularity_captured_at=None, popularity_ready=False,
        )

        item = _build_candidate_item(candidate, _CandidateEvidence(None, None, []), previous, context)

        self.assertEqual((item.popularity_rank, item.popularity_snapshot_at), (8, now))
        self.assertEqual(item.popularity_source, "new-dragon-tiger-dragon-tiger")
        self.assertEqual(item.popularity_adjustment, 0)
        self.assertIsNone(item.popularity_rank_change)
        self.assertIn("最新人气榜刷新不可用", item.data_missing)
        self.assertEqual((previous.popularity_rank, previous.popularity_snapshot_at), (12, previous_at))

    # Regression scenario: discovery item is rejected by public model.
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

    # Regression scenario: legacy snapshot fields are normalized before display.
    def test_legacy_snapshot_fields_are_normalized_before_display(self) -> None:
        snapshot_at = datetime(2026, 9, 7, 16, tzinfo=timezone.utc)
        legacy_item = {
            "base_trade_date": "2026-09-07", "symbol": "002712",
            "name": "思美传媒", "rank": 3, "base_score": 81.2,
            "refreshed_at": snapshot_at.isoformat(),
        }
        for overrides, expected_adjustment in (({}, 0), ({"draft_score": 83.2}, 2)):
            with self.subTest(overrides=overrides):
                snapshot = RecommendationIntelligenceResponse.model_validate({
                    "refresh_id": "legacy", "refreshed_at": snapshot_at,
                    "interval_minutes": 30, "status": "partial",
                    "items": [{**legacy_item, **overrides}],
                })
                self.snapshot_repo.save(snapshot)
                restored = self.snapshot_repo.get_latest_displayable()
                self.assertIsNotNone(restored)
                item = restored.model_dump(mode="json")["items"][0]
                self.assertEqual(item["rule_rank"], 3)
                self.assertEqual(item["base_rank"], 3)
                self.assertEqual(item["rule_score"], 81.2)
                self.assertEqual(item["draft_score"], overrides.get("draft_score", 81.2))
                self.assertEqual(item["dynamic_adjustment"], expected_adjustment)
                self.assertEqual(item["sector"], "")
                self.assertIsNone(item["facts_cutoff_at"])
                self.assertIsNone(item["popularity_rank"])
                self.assertFalse(item["dragon_tiger_on_list"])
                self.assertEqual(item["update_reasons"], [])
                self.assertEqual(item["data_missing"], [])

    # Regression scenario: latest displayable falls back when missed cutoff has no items.
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
        displayed = self.snapshot_repo.get_latest_displayable()
        self.assertEqual(
            displayed.model_dump(exclude={"display_context"}),
            available.model_dump(exclude={"display_context"}),
        )
        self.assertEqual(displayed.display_context.model_dump(), {
            "is_history_fallback": True,
            "latest_target_trade_date": missed.target_trade_date,
            "latest_stage": "missed_cutoff",
            "latest_refreshed_at": missed.refreshed_at,
            "latest_warnings": missed.warnings,
        })
        self.assertEqual(self.snapshot_repo.get_latest(), missed)


if __name__ == "__main__":
    unittest.main()
