import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import StockKLineFacts, StockNewsFacts, StockNewsItem
from app.repositories import SQLiteFirstBoardRepository, SQLiteStockNewsRepository
from app.services.stock_news import collect_stock_news
from app.services.sample_data import SAMPLE_EVENTS


class StockNewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"stock-news-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteStockNewsRepository(self.database_path)
        self.now = datetime(2026, 8, 31, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.addCleanup(self._cleanup_database)

    def _cleanup_database(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    def _item(self, title: str, *, hours_ago: int, url_suffix: str) -> StockNewsItem:
        return StockNewsItem(
            symbol="301489",
            name="思泉新材",
            title=title,
            summary=f"思泉新材相关资讯：{title}",
            published_at=self.now - timedelta(hours=hours_ago),
            source="测试资讯源",
            url=f"https://example.com/{url_suffix}",
            item_type="news",
            relevance_score=1.0,
            fetched_at=self.now,
        )

    def test_collects_persists_sorts_and_filters_the_requested_window(self) -> None:
        def loader(symbol: str, name: str, fetched_at: datetime) -> list[StockNewsItem]:
            self.assertEqual((symbol, name), ("301489", "思泉新材"))
            self.assertEqual(fetched_at, self.now)
            return [
                self._item("较早消息", hours_ago=24, url_suffix="older"),
                self._item("最新消息", hours_ago=1, url_suffix="latest"),
                self._item("最新消息", hours_ago=2, url_suffix="duplicate"),
                self._item("窗口外消息", hours_ago=240, url_suffix="expired"),
            ]

        response = collect_stock_news(
            symbol="301489",
            name="思泉新材",
            days=7,
            limit=10,
            repository=self.repository,
            loaders={"测试资讯源": loader},
            now=self.now,
        )

        self.assertEqual(response.cache_status, "live")
        self.assertEqual([item.title for item in response.items], ["最新消息", "较早消息"])
        self.assertEqual(response.data_missing, [])
        cached = self.repository.list_items(
            symbol="301489",
            published_since=self.now - timedelta(days=7),
            limit=10,
        )
        self.assertEqual(len(cached), 2)

    def test_returns_stale_cache_and_explicit_error_when_provider_fails(self) -> None:
        collect_stock_news(
            symbol="301489",
            name="思泉新材",
            repository=self.repository,
            loaders={"测试资讯源": lambda *_args: [self._item("缓存消息", hours_ago=1, url_suffix="cached")]},
            now=self.now,
        )

        def failing_loader(*_args) -> list[StockNewsItem]:
            raise RuntimeError("upstream unavailable")

        response = collect_stock_news(
            symbol="301489",
            name="思泉新材",
            repository=self.repository,
            loaders={"测试资讯源": failing_loader},
            now=self.now + timedelta(minutes=15),
        )

        self.assertEqual(response.cache_status, "stale")
        self.assertEqual(response.items[0].title, "缓存消息")
        self.assertTrue(any("upstream unavailable" in item for item in response.data_missing))

    def test_stock_activity_combines_close_facts_events_and_news(self) -> None:
        news = StockNewsFacts(
            symbol="301489",
            name="思泉新材",
            fetched_at=self.now,
            window_days=7,
            cache_status="live",
            sources=["测试资讯源"],
            items=[self._item("公司动态", hours_ago=1, url_suffix="activity")],
        )
        kline = StockKLineFacts(
            symbol="301489",
            requested_days=20,
            requested_end_date=date(2026, 5, 15),
            data_as_of=date(2026, 5, 15),
            data_fresh=True,
            trend="rising",
            latest_close=99.0,
            return_5d_pct=8.0,
            return_20d_pct=15.0,
            volume_ratio_5d=1.6,
            max_drawdown_pct=3.0,
        )
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=SQLiteFirstBoardRepository(self.database_path),
        )
        with (
            patch(
                "app.agents.tools.collect_stock_news",
                return_value=news,
            ),
            patch.object(
                registry,
                "stock_kline",
                return_value=ToolResult(
                    name="stock_kline",
                    input={"symbol": "301489", "days": 20},
                    output=kline,
                    summary="测试 K 线。",
                    trace_output=kline.model_dump(mode="json"),
                ),
            ),
        ):
            result = registry.stock_activity("301489", days=7, news_limit=8)

        self.assertEqual(result.output.symbol, "301489")
        self.assertEqual(result.output.name, "思泉新材")
        self.assertEqual(result.output.kline.trend, "rising")
        self.assertTrue(result.output.recent_limit_up_events)
        self.assertEqual(result.output.news.items[0].title, "公司动态")
        self.assertNotIn("bars", result.trace_output["kline"])

    def test_stock_news_identity_can_resolve_name_outside_local_limit_up_pool(self) -> None:
        class SymbolDirectory:
            calls = 0

            def collect_a_share_symbol_names(self) -> dict[str, str]:
                self.calls += 1
                return {"300750": "宁德时代", "000001": "平安银行"}

        directory = SymbolDirectory()
        registry = AgentToolRegistry(
            events=[],
            first_board_repository=SQLiteFirstBoardRepository(self.database_path),
            hithink_collector=directory,  # type: ignore[arg-type]
        )

        self.assertEqual(
            registry.resolve_stock_identity("宁德时代最近有什么新闻"),
            ("300750", "宁德时代"),
        )
        self.assertEqual(
            registry.resolve_stock_identity("300750"),
            ("300750", "宁德时代"),
        )
        self.assertEqual(directory.calls, 1)


if __name__ == "__main__":
    unittest.main()
