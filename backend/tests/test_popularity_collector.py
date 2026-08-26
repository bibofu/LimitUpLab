import unittest
from unittest.mock import MagicMock, patch

from app.collectors import collect_eastmoney_hot_stock_ranking


class PopularityCollectorTest(unittest.TestCase):
    @patch("app.collectors.first_board_enrichment_collector.requests.post")
    def test_eastmoney_top100_keeps_names_and_rank_order(self, post: MagicMock) -> None:
        response = MagicMock()
        response.json.return_value = {
            "data": [
                {"sc": "SH603618", "rk": 1, "hisRc": 6},
                {"sc": "SZ000001", "rk": 2, "hisRc": 1},
            ]
        }
        post.return_value = response

        snapshot = collect_eastmoney_hot_stock_ranking(
            limit=100,
            name_resolver=lambda: {
                "603618": "杭电股份",
                "000001": "平安银行",
            },
        )

        self.assertEqual(snapshot.source, "eastmoney")
        self.assertEqual([item.rank for item in snapshot.items], [1, 2])
        self.assertEqual(snapshot.items[0].name, "杭电股份")
        self.assertEqual(snapshot.items[0].rank_change, 5)
        self.assertEqual(snapshot.items[0].rank_trend, "up")
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
