import unittest
from datetime import date, time

from app.collectors.akshare_limit_up_collector import (
    _parse_board_height,
    _parse_hhmmss,
    parse_akshare_trade_date,
)


class AKShareLimitUpCollectorTest(unittest.TestCase):
    def test_parse_akshare_trade_date(self) -> None:
        self.assertEqual(parse_akshare_trade_date("20260515"), date(2026, 5, 15))

    def test_parse_akshare_trade_date_rejects_invalid_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_akshare_trade_date("2026-05-15")

    def test_parse_hhmmss(self) -> None:
        self.assertEqual(_parse_hhmmss("092500"), time(9, 25))
        self.assertEqual(_parse_hhmmss(93046), time(9, 30, 46))

    def test_parse_board_height(self) -> None:
        self.assertEqual(_parse_board_height("5/5"), 5)
        self.assertEqual(_parse_board_height("2/1"), 1)
        self.assertEqual(_parse_board_height(""), 1)


if __name__ == "__main__":
    unittest.main()
