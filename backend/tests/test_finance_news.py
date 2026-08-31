import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.models import FinanceNewsItem
from app.routers.market import _include_market_news, get_finance_news
from app.services.finance_news import collect_finance_news


class FinanceNewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 24, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    def _item(
        self,
        title: str,
        summary: str,
        *,
        source: str,
        minutes_ago: int,
        category: str,
        relevance: float,
    ) -> FinanceNewsItem:
        return FinanceNewsItem(
            title=title,
            summary=summary,
            published_at=self.now - timedelta(minutes=minutes_ago),
            source=source,
            url=f"https://example.com/{source}/{minutes_ago}",
            category=category,
            relevance_score=relevance,
        )

    def test_aggregates_deduplicates_and_ranks_market_news(self) -> None:
        def eastmoney_loader() -> list[FinanceNewsItem]:
            return [
                self._item(
                    "国产算力产业发布新进展",
                    "国产算力产业链出现新进展。",
                    source="东方财富",
                    minutes_ago=20,
                    category="产业",
                    relevance=4,
                ),
                self._item(
                    "人工智能走进课堂",
                    "某地学校开展人工智能课程。",
                    source="东方财富",
                    minutes_ago=5,
                    category="其他",
                    relevance=-4,
                ),
            ]

        def tonghuashun_loader() -> list[FinanceNewsItem]:
            return [
                self._item(
                    "国产算力产业发布新进展",
                    "国产算力产业链出现新进展，涉及芯片、服务器和数据中心。",
                    source="同花顺",
                    minutes_ago=18,
                    category="产业",
                    relevance=4,
                ),
                self._item(
                    "央行公布人民币中间价",
                    "央行公布当日人民币汇率中间价。",
                    source="同花顺",
                    minutes_ago=10,
                    category="宏观",
                    relevance=4,
                ),
            ]

        response = collect_finance_news(
            limit=8,
            hours=24,
            loaders={"东方财富": eastmoney_loader, "同花顺": tonghuashun_loader},
            now=self.now,
        )

        self.assertCountEqual(response.sources, ["东方财富", "同花顺"])
        self.assertEqual(len(response.items), 2)
        duplicate = next(item for item in response.items if "国产算力" in item.title)
        self.assertEqual(duplicate.source, "同花顺")
        self.assertIn("数据中心", duplicate.summary)
        self.assertFalse(any("课堂" in item.title for item in response.items))

    def test_keeps_working_when_one_source_fails(self) -> None:
        def failing_loader() -> list[FinanceNewsItem]:
            raise RuntimeError("upstream unavailable")

        def working_loader() -> list[FinanceNewsItem]:
            return [
                self._item(
                    "A股市场开盘",
                    "沪深市场正常开盘。",
                    source="东方财富",
                    minutes_ago=5,
                    category="A股",
                    relevance=4,
                )
            ]

        response = collect_finance_news(
            loaders={"同花顺": failing_loader, "东方财富": working_loader},
            now=self.now,
        )

        self.assertEqual(response.sources, ["东方财富"])
        self.assertEqual(response.items[0].title, "A股市场开盘")

    def test_market_news_route_returns_structured_feed(self) -> None:
        expected = collect_finance_news(
            limit=200,
            loaders={
                "东方财富": lambda: [
                    self._item(
                        f"A股盘前政策快讯 {index}",
                        "盘前发布最新政策信息。",
                        source="东方财富",
                        minutes_ago=index,
                        category="A股",
                        relevance=4,
                    )
                    for index in range(12)
                ]
            },
            now=self.now,
        )
        with patch("app.routers.market.collect_finance_news", return_value=expected) as loader:
            response = get_finance_news(page=2, page_size=5)

        self.assertEqual(response.page, 2)
        self.assertEqual(response.page_size, 5)
        self.assertEqual(response.total, 12)
        self.assertEqual(response.total_pages, 3)
        self.assertEqual(len(response.items), 5)
        self.assertGreater(response.items[0].published_at, response.items[-1].published_at)
        loader.assert_called_once_with(limit=2000, hours=24)

    def test_market_news_route_hides_upstream_error(self) -> None:
        with patch(
            "app.routers.market.collect_finance_news",
            side_effect=RuntimeError("provider detail"),
        ):
            with self.assertRaises(HTTPException) as raised:
                get_finance_news(page=1, page_size=10)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("provider detail", str(raised.exception.detail))

    def test_market_feed_removes_foreign_noise_but_keeps_us_and_hk_equities(self) -> None:
        domestic = self._item(
            "商务部发布商品消费实施意见",
            "国内消费政策迎来更新。",
            source="东方财富",
            minutes_ago=1,
            category="宏观",
            relevance=4,
        )
        geopolitical = self._item(
            "瑞典向法国采购新型护卫舰",
            "欧洲防务消息。",
            source="东方财富",
            minutes_ago=2,
            category="其他",
            relevance=0,
        )
        us_equity = self._item(
            "美股三大指数盘前上涨",
            "纳斯达克指数期货走强。",
            source="同花顺",
            minutes_ago=3,
            category="海外市场",
            relevance=4,
        )
        hk_equity = self._item(
            "港股恒生科技指数收涨",
            "香港股市科技板块表现活跃。",
            source="同花顺",
            minutes_ago=4,
            category="海外市场",
            relevance=4,
        )
        japan_equity = self._item(
            "日本日经指数收跌",
            "日本股市全天走弱。",
            source="同花顺",
            minutes_ago=5,
            category="海外市场",
            relevance=2,
        )
        foreign_government = self._item(
            "水利部通报尼泊尔冰川变化",
            "尼泊尔相关冰川仍有崩落可能。",
            source="东方财富",
            minutes_ago=6,
            category="其他",
            relevance=0,
        )
        a_share_impact = self._item(
            "美国政策变化影响A股科技板块",
            "沪深市场科技股出现波动。",
            source="东方财富",
            minutes_ago=7,
            category="A股",
            relevance=4,
        )

        self.assertTrue(_include_market_news(domestic))
        self.assertFalse(_include_market_news(geopolitical))
        self.assertTrue(_include_market_news(us_equity))
        self.assertTrue(_include_market_news(hk_equity))
        self.assertFalse(_include_market_news(japan_equity))
        self.assertFalse(_include_market_news(foreign_government))
        self.assertTrue(_include_market_news(a_share_impact))


if __name__ == "__main__":
    unittest.main()
