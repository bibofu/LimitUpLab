import unittest
from datetime import date, datetime
from unittest.mock import patch

from app.collectors.stock_kline_collector import (
    _aggregate_intraday_rows,
    _normalize_stock_symbol,
    _parse_sina_intraday_payload,
    _parse_tencent_spot_line,
    _sina_intraday_datalen,
    build_stock_close_snapshot,
    collect_stock_intraday_kline,
    collect_stock_kline,
    _parse_datetime,
)
from app.models import StockIntradayKLineBar, StockKLineBar


class StockKLineCollectorTest(unittest.TestCase):
    @patch("app.collectors.stock_kline_collector.ak.stock_zh_a_hist_tx")
    def test_daily_collector_includes_end_date_and_excludes_later_rows(self, history) -> None:
        class Frame:
            def to_dict(self, _orient: str):
                return [
                    {
                        "date": item_date,
                        "open": 10,
                        "close": 10.5,
                        "high": 11,
                        "low": 9.5,
                        "amount": 1_000,
                    }
                    for item_date in (
                        date(2026, 8, 17),
                        date(2026, 8, 18),
                        date(2026, 8, 19),
                    )
                ]

        history.return_value = Frame()

        bars = collect_stock_kline("002365", days=5, end_date=date(2026, 8, 18))

        self.assertEqual(
            [item.trade_date for item in bars],
            [date(2026, 8, 17), date(2026, 8, 18)],
        )
        self.assertEqual(history.call_args.kwargs["end_date"], "20260819")

    def test_normalize_stock_symbol(self) -> None:
        self.assertEqual(_normalize_stock_symbol("001259"), "sz001259")
        self.assertEqual(_normalize_stock_symbol("600519"), "sh600519")
        self.assertEqual(_normalize_stock_symbol("sz001259"), "sz001259")

    def test_normalize_stock_symbol_rejects_invalid_symbol(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_stock_symbol("abc")

    def test_parse_datetime(self) -> None:
        self.assertEqual(
            _parse_datetime("2026-05-20 09:35:00"),
            datetime(2026, 5, 20, 9, 35),
        )

    def test_sina_payload_is_parsed_without_akshare_daily_request(self) -> None:
        class Response:
            text = (
                '/* guard */\n=([{"day":"2026-08-31 09:31:00",'
                '"open":"10.00","high":"10.20","low":"9.90",'
                '"close":"10.10","volume":"1000","amount":"10100"}]);'
            )

            @staticmethod
            def raise_for_status() -> None:
                return None

        class Session:
            trust_env = True
            params: dict[str, str] | None = None

            def get(self, _url: str, *, params, timeout: int):
                self.params = params
                self.timeout = timeout
                return Response()

            @staticmethod
            def close() -> None:
                return None

        session = Session()
        with patch(
            "app.collectors.stock_kline_collector.requests.Session",
            return_value=session,
        ):
            bars = collect_stock_intraday_kline(
                "002328",
                trade_date=date(2026, 8, 31),
                period=1,
            )

        self.assertFalse(session.trust_env)
        self.assertEqual(session.timeout, 8)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 10.1)
        self.assertLessEqual(int(session.params["datalen"]), 1970)  # type: ignore[index]

    def test_sina_payload_validation_and_bounded_data_length(self) -> None:
        self.assertEqual(_parse_sina_intraday_payload("=([]);"), [])
        with self.assertRaises(ValueError):
            _parse_sina_intraday_payload("not jsonp")
        self.assertLess(_sina_intraday_datalen(date.today(), 1), 300)
        self.assertEqual(_sina_intraday_datalen(date(2020, 1, 1), 1), 1970)

    def test_parse_tencent_spot_line_requires_expected_trade_date(self) -> None:
        fields = [""] * 35
        fields[2] = "002365"
        fields[3] = "15.55"
        fields[5] = "14.50"
        fields[6] = "221743"
        fields[30] = "20260818155051"
        fields[33] = "15.55"
        fields[34] = "14.42"
        line = f'v_sz002365="{"~".join(fields)}";'

        parsed = _parse_tencent_spot_line(line, date(2026, 8, 18))

        self.assertIsNotNone(parsed)
        symbol, bar = parsed  # type: ignore[misc]
        self.assertEqual(symbol, "002365")
        self.assertEqual(bar.trade_date, date(2026, 8, 18))
        self.assertEqual(bar.close, 15.55)
        self.assertEqual(bar.volume, 221_743)
        self.assertIsNone(_parse_tencent_spot_line(line, date(2026, 8, 17)))


    def test_build_stock_close_snapshot(self) -> None:
        bars = [
            StockKLineBar(
                trade_date=date(2026, 5, 19),
                open=10.0,
                close=10.0,
                high=10.5,
                low=9.8,
                volume=1000,
            ),
            StockKLineBar(
                trade_date=date(2026, 5, 20),
                open=10.2,
                close=11.0,
                high=11.0,
                low=10.1,
                volume=1500,
            ),
        ]

        snapshot = build_stock_close_snapshot("002001", bars, source="test")

        self.assertEqual(snapshot.symbol, "002001")
        self.assertEqual(snapshot.trade_date, date(2026, 5, 20))
        self.assertEqual(snapshot.close, 11.0)
        self.assertEqual(snapshot.previous_close, 10.0)
        self.assertEqual(snapshot.change, 1.0)
        self.assertEqual(snapshot.change_pct, 10.0)
        self.assertEqual(snapshot.volume, 1500)
        self.assertEqual(snapshot.source, "test")
    def test_aggregate_intraday_rows(self) -> None:
        rows = [
            StockIntradayKLineBar(
                timestamp=datetime(2026, 5, 20, 9, 31 + index),
                open=10 + index,
                close=11 + index,
                high=12 + index,
                low=9 + index,
                volume=100,
                amount=1_000,
            )
            for index in range(5)
        ]

        [bar] = _aggregate_intraday_rows(rows, period=5)

        self.assertEqual(bar.timestamp, datetime(2026, 5, 20, 9, 35))
        self.assertEqual(bar.open, 10)
        self.assertEqual(bar.close, 15)
        self.assertEqual(bar.high, 16)
        self.assertEqual(bar.low, 9)
        self.assertEqual(bar.volume, 500)
        self.assertEqual(bar.amount, 5_000)


if __name__ == "__main__":
    unittest.main()

