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


if __name__ == "__main__":
    unittest.main()
