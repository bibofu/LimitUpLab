import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from app.collectors.first_board_enrichment_collector import (
    DragonTigerFact,
    PopularityFact,
)
from app.collectors.hithink_finance_collector import (
    HithinkIncomeStatementFact,
    HithinkMarketSnapshot,
    HithinkMarketSnapshotFact,
)
from app.models import LimitUpEvent, StockNewsFacts, StockNewsItem
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteRecommendationIntelligenceRepository,
)
from app.routers.agents import get_recommendation_intelligence
from app.services.recommendation_intelligence import (
    _BaseCandidate,
    _load_base_candidates,
    _news_adjustment,
    finalize_recommendation_intelligence,
    refresh_recommendation_intelligence,
    should_finalize_recommendation_intelligence,
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
            _BaseCandidate(
                "discovery", date(2026, 8, 31), "600640", "国脉文化",
                "文化传媒", "区间突破", 1, 85.3,
            ),
            _BaseCandidate(
                "relay", date(2026, 8, 31), "002712", "思美传媒",
                "文化传媒", "低位启动", 1, 81.2,
            ),
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
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
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
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
            )

        self.assertEqual({item.strategy for item in first.items}, {"discovery", "relay"})
        self.assertEqual(first.items[0].latest_news[0].title, "公司最新动态")
        report = first.items[0].financial_report
        self.assertIsNotNone(report)
        self.assertEqual(report.operating_income_yoy_pct, 25.0)
        self.assertEqual(report.net_profit_yoy_pct, 100.0)
        self.assertEqual(first.items[0].financial_adjustment, 3.0)
        self.assertEqual(first.items[0].draft_score, 88.3)
        relay_item = next(item for item in first.items if item.strategy == "relay")
        self.assertEqual(relay_item.rule_score, 81.2)
        self.assertEqual(relay_item.base_score, 84.2)
        self.assertEqual(relay_item.draft_score, 84.2)
        self.assertEqual(relay_item.close_information_adjustment, 3.0)
        self.assertEqual(relay_item.financial_adjustment, 0.0)
        self.assertEqual(
            relay_item.facts_cutoff_at,
            datetime(2026, 8, 31, 15, tzinfo=timezone(timedelta(hours=8))),
        )
        self.assertIn("收盘前已知", relay_item.close_information_reasons[0])
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

    def test_recent_news_can_move_candidate_across_close_rank(self) -> None:
        now = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        candidates = [
            _BaseCandidate(
                "relay", date(2026, 8, 31), "600640", "国脉文化",
                "文化传媒", "区间突破", 2, 80.0,
            ),
            _BaseCandidate(
                "relay", date(2026, 8, 31), "002712", "思美传媒",
                "文化传媒", "低位启动", 1, 82.0,
            ),
        ]

        def news(symbol: str, name: str) -> StockNewsFacts:
            title = "公司签署重大订单" if symbol == "600640" else "股东拟减持股份"
            return StockNewsFacts(
                symbol=symbol,
                name=name,
                fetched_at=now,
                window_days=7,
                cache_status="live",
                sources=["测试资讯"],
                items=[
                    StockNewsItem(
                        symbol=symbol,
                        name=name,
                        title=title,
                        summary=title,
                        published_at=now,
                        source="测试资讯",
                        url=f"https://example.com/{symbol}",
                        item_type="news",
                        relevance_score=1,
                        fetched_at=now,
                    )
                ],
            )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=(candidates, None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=news,
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
            )

        relay = [item for item in response.items if item.strategy == "relay"]
        self.assertEqual([item.symbol for item in relay], ["600640", "002712"])
        self.assertEqual(relay[0].base_rank, 2)
        self.assertEqual(relay[0].rank, 1)
        self.assertEqual(relay[0].news_adjustment, 3.0)
        self.assertEqual(relay[1].news_adjustment, -4.0)

    def test_market_table_summary_does_not_create_company_news_adjustment(self) -> None:
        now = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
        facts = StockNewsFacts(
            symbol="000713",
            name="国投丰乐",
            fetched_at=now,
            window_days=7,
            cache_status="live",
            sources=["证券时报网"],
            items=[
                StockNewsItem(
                    symbol="000713",
                    name="国投丰乐",
                    title="今日95只股长线走稳 站上年线",
                    summary="列表中包含国投丰乐，盘中突破年线。",
                    published_at=now,
                    source="证券时报网",
                    url="https://example.com/market-table",
                    item_type="news",
                    relevance_score=0.8,
                    fetched_at=now,
                )
            ],
        )

        adjustment, reasons = _news_adjustment(facts, refreshed_at=now)

        self.assertEqual(adjustment, 0.0)
        self.assertEqual(reasons, [])

    def test_pre_close_news_is_folded_into_relay_close_score(self) -> None:
        now = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 80.0,
        )
        published_at = datetime(
            2026,
            8,
            31,
            14,
            tzinfo=timezone(timedelta(hours=8)),
        )

        def news(symbol: str, name: str) -> StockNewsFacts:
            return StockNewsFacts(
                symbol=symbol,
                name=name,
                fetched_at=now,
                window_days=7,
                cache_status="live",
                sources=["测试资讯"],
                items=[
                    StockNewsItem(
                        symbol=symbol,
                        name=name,
                        title="公司获得重大订单",
                        summary="公司获得重大订单",
                        published_at=published_at,
                        source="测试资讯",
                        url="https://example.com/pre-close",
                        item_type="news",
                        relevance_score=1,
                        fetched_at=now,
                    )
                ],
            )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=news,
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
            )

        item = response.items[0]
        self.assertEqual(item.rule_score, 80.0)
        self.assertEqual(item.base_score, 83.0)
        self.assertEqual(item.draft_score, 83.0)
        self.assertEqual(item.close_information_adjustment, 3.0)
        self.assertEqual(item.news_adjustment, 0.0)
        self.assertIn("收盘前已知", item.close_information_reasons[0])

    def test_post_close_financial_report_can_adjust_relay_draft(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 80.0,
        )

        def financials(thscode: str) -> list[HithinkIncomeStatementFact]:
            return [
                HithinkIncomeStatementFact(
                    thscode=thscode,
                    fiscal_year=2026,
                    fiscal_period="Q2",
                    report_date=date(2026, 9, 1),
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

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=financials,
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
            )

        item = response.items[0]
        self.assertEqual(item.base_score, 80.0)
        self.assertEqual(item.close_information_adjustment, 0.0)
        self.assertEqual(item.financial_adjustment, 3.0)
        self.assertEqual(item.draft_score, 83.0)
        self.assertIn("收盘后新增", item.update_reasons[0])

    def test_new_dragon_tiger_and_popularity_rise_adjust_relay_draft(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        base_snapshot_at = datetime(
            2026,
            8,
            31,
            15,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )
        candidate = _BaseCandidate(
            "relay",
            date(2026, 8, 31),
            "600640",
            "国脉文化",
            "文化传媒",
            "区间突破",
            1,
            80.0,
            amount=800_000_000,
            popularity_baseline_ready=True,
            popularity_rank=65,
            popularity_snapshot_at=base_snapshot_at,
        )
        dragon_tiger = DragonTigerFact(
            symbol="600640",
            buy_amount=120_000_000,
            sell_amount=60_000_000,
            net_buy_amount=60_000_000,
            float_market_cap=None,
            reason="日涨幅偏离值达 7%",
            source="hithink-finance",
        )
        popularity = PopularityFact(
            symbol="600640",
            rank=10,
            rank_change=55,
            captured_at=now,
            source="hithink-finance",
        )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(
                    return_value={"600640": dragon_tiger}
                ),
                popularity_collector=Mock(
                    return_value={"600640": popularity}
                ),
            )

        item = response.items[0]
        self.assertTrue(item.dragon_tiger_is_new)
        self.assertEqual(item.dragon_tiger_adjustment, 2.0)
        self.assertEqual(item.popularity_base_rank, 65)
        self.assertEqual(item.popularity_rank, 10)
        self.assertEqual(item.popularity_rank_change, 55)
        self.assertEqual(item.popularity_adjustment, 2.0)
        self.assertEqual(item.dynamic_adjustment, 4.0)
        self.assertEqual(item.draft_score, 84.0)
        self.assertTrue(any("新上龙虎榜" in reason for reason in item.update_reasons))
        self.assertTrue(any("人气变化" in reason for reason in item.update_reasons))

    def test_known_dragon_tiger_fact_is_not_scored_twice(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay",
            date(2026, 8, 31),
            "600640",
            "国脉文化",
            "文化传媒",
            "区间突破",
            1,
            80.0,
            amount=800_000_000,
            dragon_tiger_on_list=True,
            dragon_tiger_net_buy_amount=60_000_000,
        )
        current = DragonTigerFact(
            symbol="600640",
            buy_amount=120_000_000,
            sell_amount=60_000_000,
            net_buy_amount=60_000_000,
            float_market_cap=None,
            reason=None,
        )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(return_value={"600640": current}),
                popularity_collector=Mock(
                    return_value=self._unrelated_popularity(now)
                ),
            )

        item = response.items[0]
        self.assertTrue(item.dragon_tiger_on_list)
        self.assertFalse(item.dragon_tiger_is_new)
        self.assertEqual(item.dragon_tiger_adjustment, 0.0)
        self.assertEqual(item.dynamic_adjustment, 0.0)
        self.assertEqual(item.draft_score, 80.0)

    def test_missing_popularity_baseline_is_exposed_without_guessing(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 80.0,
        )
        popularity = PopularityFact(
            symbol="600640",
            rank=8,
            rank_change=40,
            captured_at=now,
        )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(return_value={}),
                popularity_collector=Mock(
                    return_value={"600640": popularity}
                ),
            )

        item = response.items[0]
        self.assertEqual(item.popularity_rank, 8)
        self.assertEqual(item.popularity_adjustment, 0.0)
        self.assertIn("收盘人气基线不可用", item.data_missing)

    def test_low_position_pool_uses_current_popularity_as_reranking_signal(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "discovery", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 80.0,
        )
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], date(2026, 8, 31), None, []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
                popularity_collector=Mock(
                    return_value={
                        "600640": PopularityFact(
                            symbol="600640",
                            rank=5,
                            rank_change=4,
                            captured_at=now,
                        )
                    }
                ),
            )

        item = response.items[0]
        self.assertEqual(item.popularity_rank, 5)
        self.assertEqual(item.popularity_adjustment, 2.0)
        self.assertEqual(item.draft_score, 82.0)

    def test_total_post_close_adjustment_is_capped(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "relay",
            date(2026, 8, 31),
            "600640",
            "国脉文化",
            "文化传媒",
            "区间突破",
            1,
            80.0,
            amount=800_000_000,
            popularity_baseline_ready=True,
            popularity_rank=65,
            popularity_snapshot_at=datetime(
                2026,
                8,
                31,
                15,
                30,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )
        dragon_tiger = DragonTigerFact(
            symbol="600640",
            buy_amount=120_000_000,
            sell_amount=60_000_000,
            net_buy_amount=60_000_000,
            float_market_cap=None,
            reason=None,
        )

        def positive_news(symbol: str, name: str) -> StockNewsFacts:
            facts = self._news(symbol, name)
            return facts.model_copy(
                update={
                    "items": [
                        facts.items[0].model_copy(
                            update={
                                "title": "公司签署重大订单",
                                "summary": "公司签署重大订单",
                                "published_at": now,
                            }
                        )
                    ]
                }
            )

        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], None, date(2026, 8, 31), []),
        ):
            response = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=positive_news,
                financial_collector=Mock(return_value=[]),
                dragon_tiger_collector=Mock(
                    return_value={"600640": dragon_tiger}
                ),
                popularity_collector=Mock(
                    return_value={
                        "600640": PopularityFact(
                            symbol="600640",
                            rank=10,
                            rank_change=55,
                            captured_at=now,
                        )
                    }
                ),
            )

        item = response.items[0]
        self.assertEqual(item.news_adjustment, 3.0)
        self.assertEqual(item.dragon_tiger_adjustment, 2.0)
        self.assertEqual(item.popularity_adjustment, 2.0)
        self.assertEqual(item.dynamic_adjustment, 6.0)
        self.assertEqual(item.draft_score, 86.0)
        self.assertTrue(any("受 ±6 分约束" in reason for reason in item.update_reasons))

    def test_relay_base_pool_excludes_chinext_candidates(self) -> None:
        trade_date = date(2026, 9, 1)
        main_board = LimitUpEvent(
            symbol="002001",
            name="主板样本",
            trade_date=trade_date,
            first_limit_time=time(9, 40),
            last_limit_time=time(9, 40),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=800_000_000,
            turnover_rate=6.0,
            industry="机器人",
            concept="机器人",
            next_open_pct=0.0,
            next_high_pct=0.0,
            next_close_pct=0.0,
            three_day_return_pct=0.0,
            five_day_return_pct=0.0,
            continued_next_day=False,
        )
        chinext = main_board.model_copy(
            update={"symbol": "300189", "name": "创业板样本"}
        )
        self.limit_repo.replace_events([main_board, chinext])

        candidates, _, relay_date, _ = _load_base_candidates(
            limit_up_repository=self.limit_repo,
            first_board_repository=self.first_repo,
            discovery_repository=self.discovery_repo,
        )

        relay_symbols = [
            item.symbol for item in candidates if item.strategy == "relay"
        ]
        self.assertEqual(relay_date, trade_date)
        self.assertEqual(relay_symbols, ["002001"])

    def test_finalization_freezes_one_version_and_trims_display_items(self) -> None:
        now = datetime(
            2026, 9, 1, 9, 0,
            tzinfo=timezone(timedelta(hours=8)),
        )
        candidate = _BaseCandidate(
            "discovery", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 85.3,
        )
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], date(2026, 8, 31), None, []),
        ):
            draft = refresh_recommendation_intelligence(
                now=now - timedelta(minutes=30),
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
            )

        self.assertTrue(
            should_finalize_recommendation_intelligence(draft, now=now)
        )
        final = finalize_recommendation_intelligence(
            draft,
            now=now,
            limit_up_repository=self.limit_repo,
            first_board_repository=self.first_repo,
            snapshot_repository=self.snapshot_repo,
        )
        repeated = finalize_recommendation_intelligence(
            draft.model_copy(update={"refresh_id": "different"}),
            now=now + timedelta(minutes=30),
            limit_up_repository=self.limit_repo,
            first_board_repository=self.first_repo,
            snapshot_repository=self.snapshot_repo,
        )

        self.assertEqual(final.stage, "final")
        self.assertEqual(final.finalized_at, now)
        self.assertEqual(repeated.refresh_id, final.refresh_id)
        self.assertEqual(self.snapshot_repo.get_latest().stage, "final")

    def test_repository_records_changes_without_periodic_full_snapshots(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        candidate = _BaseCandidate(
            "discovery", date(2026, 8, 31), "600640", "国脉文化",
            "文化传媒", "区间突破", 1, 85.3,
        )
        with patch(
            "app.services.recommendation_intelligence._load_base_candidates",
            return_value=([candidate], date(2026, 8, 31), None, []),
        ):
            first = refresh_recommendation_intelligence(
                now=now,
                max_workers=1,
                limit_up_repository=self.limit_repo,
                first_board_repository=self.first_repo,
                discovery_repository=self.discovery_repo,
                snapshot_repository=self.snapshot_repo,
                quote_collector=Mock(return_value=self._quotes(now)),
                news_collector=Mock(return_value=self._empty_news(now)),
                financial_collector=Mock(return_value=[]),
            )
            second = first.model_copy(
                update={
                    "refresh_id": "changed-refresh",
                    "refreshed_at": now + timedelta(minutes=30),
                    "items": [
                        first.items[0].model_copy(update={"rank": 2})
                    ],
                }
            )
            self.snapshot_repo.save(second)

        changes = self.snapshot_repo.list_changes(
            strategy="discovery",
            base_trade_date="2026-08-31",
            symbol="600640",
        )
        self.assertEqual(changes[-1]["changes"]["rank"], {"old": 1, "new": 2})

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
    def _empty_news(now: datetime) -> StockNewsFacts:
        return StockNewsFacts(
            symbol="600640",
            name="国脉文化",
            fetched_at=now,
            window_days=7,
            cache_status="live",
            sources=["测试资讯"],
            items=[],
        )

    @staticmethod
    def _unrelated_popularity(now: datetime) -> dict[str, PopularityFact]:
        return {
            "000001": PopularityFact(
                symbol="000001",
                rank=100,
                rank_change=0,
                captured_at=now,
            )
        }

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
