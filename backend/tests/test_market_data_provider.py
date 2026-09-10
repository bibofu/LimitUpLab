import unittest
from datetime import date, datetime, timezone

from app.collectors.hithink_finance_collector import (
    HithinkDailyBarFact,
    HithinkFinanceError,
    HithinkMarketSnapshot,
    HithinkMarketSnapshotFact,
    HithinkStockHistory,
)
from app.models import StockKLineBar
from app.services.market_data_provider import (
    HITHINK_HISTORY_SOURCE,
    HITHINK_SNAPSHOT_SOURCE,
    MarketDataProvider,
)


class StubHithink:
    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self, *, fail_history: bool = False) -> None:
        self.fail_history = fail_history
        self.history_calls: list[dict[str, object]] = []

    # Build the HithinkStockHistory fixture used by the surrounding regression scenario.
    def collect_stock_history(self, thscode: str, **kwargs) -> HithinkStockHistory:
        self.history_calls.append({"thscode": thscode, **kwargs})
        if self.fail_history:
            raise HithinkFinanceError("temporary failure")
        return HithinkStockHistory(
            thscode=thscode,
            start_date=kwargs["start_date"],
            end_date=kwargs["end_date"],
            adjustment=kwargs["adjustment"],
            items=[
                HithinkDailyBarFact(
                    trade_date=kwargs["end_date"],
                    open=10,
                    high=11,
                    low=9.5,
                    close=10.5,
                    volume=120_000,
                    turnover=1_260_000,
                )
            ],
        )

    # Build the HithinkMarketSnapshot fixture used by the surrounding regression scenario.
    def collect_market_snapshots(self, thscodes: list[str]) -> HithinkMarketSnapshot:
        return HithinkMarketSnapshot(
            captured_at=datetime(2026, 9, 10, 8, tzinfo=timezone.utc),
            items=[
                HithinkMarketSnapshotFact(
                    symbol="002491",
                    thscode=thscodes[0],
                    last_price=10.5,
                    change_pct=5,
                    turnover=1_260_000,
                    volume=120_000,
                    open_price=10,
                    high_price=11,
                    low_price=9.5,
                    previous_close=10,
                )
            ],
        )


class MarketDataProviderTest(unittest.TestCase):
    # Regression scenario: hithink history is primary unadjusted and normalizes volume.
    def test_hithink_history_is_primary_unadjusted_and_normalizes_volume(self) -> None:
        hithink = StubHithink()
        provider = MarketDataProvider(hithink=hithink)  # type: ignore[arg-type]

        bars = provider.collect_history("002491", days=5, end_date=date(2026, 9, 10))

        self.assertEqual(hithink.history_calls[0]["thscode"], "002491.SZ")
        self.assertEqual(hithink.history_calls[0]["adjustment"], "none")
        self.assertEqual(bars[0].volume, 1_200)
        self.assertEqual(bars[0].amount, 1_260_000)
        self.assertEqual(bars[0].source, HITHINK_HISTORY_SOURCE)

    # Regression scenario: history falls back to tencent with source preserved.
    def test_history_falls_back_to_tencent_with_source_preserved(self) -> None:
        fallback_calls: list[tuple[str, int, date | None]] = []

        # Prepare the fallback fixture or observation used by the surrounding regression scenario.
        def fallback(symbol: str, days: int, end_date: date | None):
            fallback_calls.append((symbol, days, end_date))
            return [
                StockKLineBar(
                    trade_date=date(2026, 9, 10),
                    open=10,
                    high=11,
                    low=9.5,
                    close=10.5,
                    volume=1_200,
                    source="akshare.stock_zh_a_hist_tx",
                )
            ]

        provider = MarketDataProvider(
            hithink=StubHithink(fail_history=True),  # type: ignore[arg-type]
            history_fallback=fallback,
        )

        bars = provider.collect_history("002491", days=5, end_date=date(2026, 9, 10))

        self.assertEqual(fallback_calls, [("002491", 5, date(2026, 9, 10))])
        self.assertEqual(bars[0].source, "akshare.stock_zh_a_hist_tx")

    # Regression scenario: same day snapshot is normalized to hands.
    def test_same_day_snapshot_is_normalized_to_hands(self) -> None:
        provider = MarketDataProvider(hithink=StubHithink())  # type: ignore[arg-type]

        bars = provider.collect_spot(["002491"], date(2026, 9, 10))

        self.assertEqual(bars["002491"].volume, 1_200)
        self.assertEqual(bars["002491"].source, HITHINK_SNAPSHOT_SOURCE)


if __name__ == "__main__":
    unittest.main()
