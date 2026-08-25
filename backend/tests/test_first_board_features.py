import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.database import connect, initialize_database
from app.models import StockDailyBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)
from app.services.sample_data import SAMPLE_EVENTS


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


@contextmanager
def temporary_database_path():
    database_path = TEST_TMP_ROOT / f"first-board-test-{uuid4().hex}.sqlite"
    try:
        yield database_path
    finally:
        database_path.unlink(missing_ok=True)


class FirstBoardFeaturesTest(unittest.TestCase):
    def test_initialize_database_creates_feature_tables(self) -> None:
        with temporary_database_path() as database_path:
            connection = connect(database_path)
            try:
                initialize_database(connection)
                table_names = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                connection.close()

        self.assertIn("first_board_features", table_names)
        self.assertIn("stock_daily_bars", table_names)
        self.assertIn("first_board_outcomes", table_names)
        self.assertIn("first_board_enrichment_snapshots", table_names)
        self.assertIn("agent_predictions", table_names)

    def test_initialize_database_migrates_legacy_prediction_snapshots(self) -> None:
        with temporary_database_path() as database_path:
            connection = connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE agent_predictions (
                        prediction_id TEXT PRIMARY KEY,
                        trade_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        name TEXT NOT NULL,
                        score REAL NOT NULL,
                        rating TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        scoring_version TEXT NOT NULL,
                        facts_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        risks_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(trade_date, symbol, scoring_version)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO agent_predictions VALUES (
                        'legacy', '2026-08-10', '002001', 'legacy sample',
                        80, 'A', 0.8, 'v-test', '{}', '[]', '[]',
                        '2026-08-10T10:00:00+00:00'
                    )
                    """
                )
                initialize_database(connection)

                migrated = connection.execute(
                    "SELECT prediction_source, data_as_of FROM agent_predictions"
                ).fetchone()
                self.assertEqual(migrated["prediction_source"], "historical_backtest")
                self.assertEqual(migrated["data_as_of"], "2026-08-10")
                connection.execute(
                    """
                    INSERT INTO agent_predictions VALUES (
                        'live', '2026-08-10', '002001', 'live sample',
                        81, 'A', 0.9, 'v-test', 'live', '2026-08-10',
                        '{}', '[]', '[]', '2026-08-10T11:00:00+00:00'
                    )
                    """
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_predictions").fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_build_first_board_features(self) -> None:
        features = build_first_board_features(SAMPLE_EVENTS, trade_date=date(2026, 5, 15))

        self.assertEqual([feature.symbol for feature in features], ["301489"])
        feature = features[0]
        self.assertEqual(feature.first_limit_bucket, "early")
        self.assertEqual(feature.turnover_bucket, "extreme")
        self.assertEqual(feature.amount_bucket, "medium")
        self.assertEqual(feature.market_failed_rate_bucket, "fragile")
        self.assertEqual(feature.feature_version, "first-board-feature-v1")

    def test_repository_upserts_and_lists_features(self) -> None:
        features = build_first_board_features(SAMPLE_EVENTS, trade_date=date(2026, 5, 15))

        with temporary_database_path() as database_path:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            repository.upsert_features(features)
            persisted = repository.list_features_for_date(date(2026, 5, 15))

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].symbol, "301489")
        self.assertEqual(persisted[0].market_sentiment, "cooling")

    def test_build_first_board_outcome_from_event_performance(self) -> None:
        event = next(item for item in SAMPLE_EVENTS if item.symbol == "301489")
        prices = [
            (10.0, 11.0, 9.8, 10.0),
            (11.0, 12.0, 10.5, 11.5),
            (11.4, 12.2, 10.8, 12.0),
            (11.8, 12.0, 8.8, 9.0),
        ]
        bars = [
            StockDailyBar(
                symbol=event.symbol,
                trade_date=event.trade_date + timedelta(days=index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1_000_000,
                amount=10_000_000,
                source="test",
                created_at=datetime.now(timezone.utc),
            )
            for index, (open_price, high, low, close) in enumerate(prices)
        ]
        outcome = build_first_board_outcome(
            event=event,
            bars=bars,
            future_events=SAMPLE_EVENTS,
        )

        self.assertTrue(outcome.next_day_ready)
        self.assertTrue(outcome.three_day_ready)
        self.assertTrue(outcome.outcome_ready)
        self.assertAlmostEqual(outcome.next_open_to_close_pct or 0, 4.55, places=2)
        self.assertAlmostEqual(outcome.next_open_to_low_pct or 0, -4.55, places=2)
        self.assertAlmostEqual(outcome.three_day_open_to_close_pct or 0, -18.18, places=2)
        self.assertAlmostEqual(outcome.max_drawdown_from_next_open_3d or 0, -20.0, places=2)
        self.assertTrue(outcome.promoted_to_second_board)
        self.assertEqual(outcome.outcome_version, "first-board-outcome-v2-entry-open")


if __name__ == "__main__":
    unittest.main()
