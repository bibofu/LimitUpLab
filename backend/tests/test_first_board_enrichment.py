import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.agents import build_first_board_ratings
from app.collectors import DragonTigerFact, PopularityFact
from app.models import StockKLineBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.first_board_enrichment import (
    ENRICHMENT_FEATURE_VERSION,
    refresh_first_board_enrichment_snapshots,
)
from app.services.sample_data import SAMPLE_EVENTS


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


@contextmanager
def temporary_database_path():
    database_path = TEST_TMP_ROOT / f"enrichment-test-{uuid4().hex}.sqlite"
    try:
        yield database_path
    finally:
        database_path.unlink(missing_ok=True)


def fake_kline_collector(
    symbol: str,
    days: int,
    end_date: date | None,
) -> list[StockKLineBar]:
    """Return deterministic bars so enrichment tests never call the network."""

    assert symbol == "301489"
    assert end_date is not None
    start_date = end_date - timedelta(days=days - 1)
    return [
        StockKLineBar(
            trade_date=start_date + timedelta(days=index),
            open=10 + index * 0.1,
            high=10.5 + index * 0.1,
            low=9.8 + index * 0.1,
            close=10.2 + index * 0.1,
            volume=1_000_000 + index * 10_000,
        )
        for index in range(days)
    ]


class FirstBoardEnrichmentTest(unittest.TestCase):
    def test_refresh_persists_all_rating_inputs_and_agent_uses_them(self) -> None:
        trade_date = date(2026, 5, 15)
        captured_at = datetime(2026, 5, 15, 8, tzinfo=timezone.utc)

        with temporary_database_path() as database_path:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            report = refresh_first_board_enrichment_snapshots(
                events=SAMPLE_EVENTS,
                trade_date=trade_date,
                repository=repository,
                kline_collector=fake_kline_collector,
                listing_collector=lambda: {},
                listing_detail_collector=lambda symbol: date(2020, 1, 2),
                dragon_tiger_collector=lambda value: {
                    "301489": DragonTigerFact(
                        symbol="301489",
                        buy_amount=80_000_000,
                        sell_amount=30_000_000,
                        net_buy_amount=50_000_000,
                        float_market_cap=4_200_000_000,
                        reason="日涨幅偏离值达到7%",
                    )
                },
                popularity_collector=lambda: {
                    "301489": PopularityFact(
                        symbol="301489",
                        rank=18,
                        rank_change=7,
                        captured_at=captured_at,
                    )
                },
            )

            snapshots = repository.list_enrichment_for_date(trade_date)
            ratings = build_first_board_ratings(
                SAMPLE_EVENTS,
                trade_date=trade_date,
                first_board_repository=repository,
            )

        self.assertEqual(report.snapshot_count, 1)
        self.assertEqual(report.technical_ready_count, 1)
        self.assertEqual(report.listing_date_count, 1)
        self.assertEqual(report.dragon_tiger_count, 1)
        self.assertEqual(report.popularity_count, 1)
        self.assertEqual(len(snapshots), 1)

        enrichment = snapshots[0]
        self.assertEqual(enrichment.feature_version, ENRICHMENT_FEATURE_VERSION)
        self.assertEqual(enrichment.kline_bar_count, 125)
        self.assertIsNotNone(enrichment.return_60d_pct)
        self.assertIsNotNone(enrichment.position)
        self.assertNotEqual(enrichment.position.primary.regime, "unclassified")
        self.assertEqual(enrichment.listing_date, date(2020, 1, 2))
        self.assertEqual(enrichment.float_market_cap, 4_200_000_000)
        self.assertEqual(enrichment.float_market_cap_source, "eastmoney_dragon_tiger")
        self.assertEqual(enrichment.dragon_tiger_source, "eastmoney")
        self.assertEqual(enrichment.popularity_rank, 18)
        self.assertEqual(enrichment.popularity_source, "eastmoney")

        rating = ratings.candidates[0]
        self.assertIsNotNone(rating.facts.enrichment)
        self.assertEqual(len(rating.score_breakdown), 12)
        self.assertEqual(sum(item.max_score for item in rating.score_breakdown), 100)
        self.assertNotIn("listing_date", rating.facts.data_missing)
        self.assertNotIn("limit_up_history_60d", rating.facts.data_missing)
        self.assertNotIn("position_history_120d", rating.facts.data_missing)


if __name__ == "__main__":
    unittest.main()
