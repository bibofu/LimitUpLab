import unittest
from datetime import date, timedelta

from app.collectors.sector_collector import SectorDailyRow, SectorSpotRow
from app.services.sector_performance import build_sector_performance


class SectorPerformanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date.today()
        self.spot_rows = [
            SectorSpotRow(
                sector_name="厨卫电器",
                rank=1,
                change_pct=4.57,
                amount_yi=21.64,
                net_inflow_yi=2.02,
                up_count=7,
                down_count=2,
                leader_name="亿田智能",
                leader_price=26.66,
                leader_change_pct=19.98,
                source="fake-spot",
            ),
            SectorSpotRow(
                sector_name="半导体",
                rank=87,
                change_pct=-7.44,
                amount_yi=4019.38,
                net_inflow_yi=-277.96,
                up_count=4,
                down_count=181,
                leader_name="斯达半导",
                leader_price=100.31,
                leader_change_pct=10.0,
                source="fake-spot",
            ),
        ]
        self.history_rows = [
            SectorDailyRow(
                trade_date=self.today - timedelta(days=25 - index),
                close=100 + index,
                change_pct=1.0,
                source="fake-history",
            )
            for index in range(26)
        ]

    def test_named_sector_returns_ranking_breadth_and_trend(self) -> None:
        response = build_sector_performance(
            "半导体板块",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: self.history_rows,
        )

        self.assertEqual(response.sector_name, "半导体")
        self.assertEqual(response.rank, 87)
        self.assertEqual(response.change_pct, -7.44)
        self.assertEqual(response.up_count, 4)
        self.assertEqual(response.down_count, 181)
        self.assertIsNotNone(response.return_5d_pct)
        self.assertEqual(response.sources, ["fake-spot", "fake-history"])

    def test_empty_sector_returns_market_ranking(self) -> None:
        response = build_sector_performance(
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
        )

        self.assertIsNone(response.sector_name)
        self.assertEqual(response.top_sectors[0].sector_name, "厨卫电器")
        self.assertEqual(response.bottom_sectors[0].sector_name, "半导体")

    def test_military_alias_returns_all_matching_industry_subsectors(self) -> None:
        military_rows = [
            SectorSpotRow(
                sector_name="军工装备",
                rank=45,
                change_pct=-0.59,
                amount_yi=395.7,
                net_inflow_yi=-5.37,
                up_count=28,
                down_count=53,
                leader_name="博云新材",
                leader_price=22.0,
                leader_change_pct=10.0,
                source="fake-industry",
            ),
            SectorSpotRow(
                sector_name="军工电子",
                rank=66,
                change_pct=-0.92,
                amount_yi=128.14,
                net_inflow_yi=-10.71,
                up_count=14,
                down_count=46,
                leader_name="科思科技",
                leader_price=32.25,
                leader_change_pct=2.87,
                source="fake-industry",
            ),
        ]

        response = build_sector_performance(
            "国防军工",
            trade_date=self.today,
            spot_collector=lambda: military_rows,
            history_collector=lambda _name, _start, _end: [],
        )

        self.assertEqual(response.sector_name, "军工")
        self.assertEqual(response.sector_type, "industry_group")
        self.assertEqual(
            [item.sector_name for item in response.matched_sectors],
            ["军工装备", "军工电子"],
        )

    def test_ai_alias_falls_back_to_concept_ranking(self) -> None:
        concept_rows = [
            SectorSpotRow(
                sector_name="人工智能",
                rank=12,
                change_pct=1.26,
                amount_yi=None,
                net_inflow_yi=None,
                up_count=91,
                down_count=43,
                leader_name="概念龙头",
                leader_price=None,
                leader_change_pct=9.8,
                source="fake-concept",
            )
        ]

        response = build_sector_performance(
            "AI",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
            concept_spot_collector=lambda: concept_rows,
            concept_history_collector=lambda _name, _start, _end: self.history_rows,
        )

        self.assertEqual(response.sector_name, "人工智能")
        self.assertEqual(response.sector_type, "concept")
        self.assertEqual(response.rank, 12)
        self.assertEqual(response.change_pct, 1.26)
        self.assertEqual(response.sources, ["fake-concept", "fake-history"])

    def test_named_concept_uses_history_when_spot_source_is_unavailable(self) -> None:
        response = build_sector_performance(
            "AI",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
            concept_spot_collector=lambda: (_ for _ in ()).throw(
                RuntimeError("concept spot unavailable")
            ),
            concept_history_collector=lambda name, _start, _end: (
                self.history_rows if name == "人工智能" else []
            ),
        )

        self.assertEqual(response.sector_name, "人工智能")
        self.assertEqual(response.sector_type, "concept")
        self.assertEqual(response.sector_count, 0)
        self.assertIn("concept-spot-unavailable", response.sources)


if __name__ == "__main__":
    unittest.main()
