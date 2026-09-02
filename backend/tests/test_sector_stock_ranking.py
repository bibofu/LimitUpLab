import unittest
from datetime import date

from app.collectors.hithink_finance_collector import (
    HithinkIndexCatalogFact,
    HithinkIndexConstituentFact,
)
from app.models import StockKLineFacts
from app.services.sector_stock_ranking import build_sector_stock_ranking


class FakeSectorCollector:
    def collect_index_catalog(self, category: str):
        if category == "industry":
            return [HithinkIndexCatalogFact("881164.TI", "游戏", "industry")]
        return [HithinkIndexCatalogFact("885946.TI", "云游戏", "cn_concept")]

    def collect_index_constituents(self, index):
        self.selected = index
        return [
            HithinkIndexConstituentFact("000001", "000001.SZ", "上升样本"),
            HithinkIndexConstituentFact("000002", "000002.SZ", "震荡样本"),
            HithinkIndexConstituentFact("000003", "000003.SZ", "回落样本"),
        ]


def fake_kline_builder(*, symbol, days, end_date, repository):
    del repository
    values = {
        "000001": ("rising", 8.0, 18.0, 1.4, -5.0),
        "000002": ("oscillating", 0.0, 0.0, 1.0, -8.0),
        "000003": ("falling", -6.0, -15.0, 0.8, -18.0),
    }
    trend, return_5d, return_20d, volume_ratio, drawdown = values[symbol]
    return StockKLineFacts(
        symbol=symbol,
        requested_days=days,
        requested_end_date=end_date,
        data_as_of=end_date,
        data_fresh=True,
        trend=trend,
        latest_close=10.0,
        return_5d_pct=return_5d,
        return_20d_pct=return_20d,
        ma5=10.0,
        ma10=9.5,
        ma20=9.0,
        volume_ratio_5d=volume_ratio,
        max_drawdown_pct=drawdown,
    )


class SectorStockRankingTests(unittest.TestCase):
    def test_resolves_exact_industry_and_ranks_completed_trends(self):
        collector = FakeSectorCollector()
        result = build_sector_stock_ranking(
            sector="游戏板块",
            end_date=date(2026, 9, 1),
            days=20,
            limit=2,
            collector=collector,
            repository=object(),
            facts_builder=fake_kline_builder,
            max_workers=2,
        )

        self.assertEqual(result.sector_name, "游戏")
        self.assertEqual(result.sector_category, "industry")
        self.assertEqual(result.member_count, 3)
        self.assertEqual(result.analyzed_count, 3)
        self.assertEqual([item.symbol for item in result.items], ["000001", "000002"])
        self.assertGreater(result.items[0].trend_score, result.items[1].trend_score)

    def test_rejects_unknown_sector_instead_of_guessing(self):
        with self.assertRaisesRegex(ValueError, "未找到"):
            build_sector_stock_ranking(
                sector="不存在的行业",
                end_date=date(2026, 9, 1),
                collector=FakeSectorCollector(),
                repository=object(),
                facts_builder=fake_kline_builder,
            )


if __name__ == "__main__":
    unittest.main()
