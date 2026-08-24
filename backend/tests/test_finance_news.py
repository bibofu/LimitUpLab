import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import FinanceNewsItem
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


if __name__ == "__main__":
    unittest.main()
