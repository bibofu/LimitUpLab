
from database_support import isolated_database_url, release_database_url
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from app.database import connect, initialize_database
from app.models import AgentPrediction, StockDailyBar
from app.repositories import FirstBoardRepository
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)
from app.services.sample_data import SAMPLE_EVENTS


def historical_prediction():
    return AgentPrediction(
        prediction_id="historical", trade_date=date(2026, 8, 10), symbol="002001",
        name="fixture", score=80, rating="A", confidence=0.8, scoring_version="v-test",
        prediction_source="historical_backtest", data_as_of=date(2026, 8, 10),
        facts_json={}, reasons=[], risks=[], created_at=datetime(2026, 8, 10, 10, tzinfo=timezone.utc),
    )


# Prepare the temporary PostgreSQL schema fixture or observation used by the surrounding regression
# scenario.
@contextmanager
def temporary_database_url():
    database_url = isolated_database_url()
    try:
        yield database_url
    finally:
        release_database_url(database_url)


class FirstBoardFeaturesTest(unittest.TestCase):
    # Alembic creates the complete current feature schema before runtime checks.
    def test_alembic_schema_contains_feature_tables(self) -> None:
        with temporary_database_url() as database_url:
            connection = connect(database_url)
            try:
                initialize_database(connection)
                table_names = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema()"
                    ).fetchall()
                }
            finally:
                connection.close()

        self.assertIn("first_board_features", table_names)
        self.assertIn("stock_daily_bars", table_names)
        self.assertIn("stock_intraday_bars", table_names)
        self.assertIn("first_board_outcomes", table_names)
        self.assertIn("first_board_enrichment_snapshots", table_names)
        self.assertIn("agent_predictions", table_names)
        self.assertIn("agent_live_prediction_snapshots", table_names)

    # Runtime checks reject stale schemas rather than rewriting old predictions.
    def test_readiness_rejects_stale_revision_without_rewriting_predictions(self) -> None:
        with temporary_database_url() as database_url:
            prediction = historical_prediction()
            repository = FirstBoardRepository(database_url)
            repository.upsert_predictions([prediction])
            connection = connect(database_url)
            try:
                before = dict(connection.execute("SELECT * FROM agent_predictions").fetchone())
                connection.execute("UPDATE alembic_version SET version_num='old_revision'")
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "schema is not current"):
                    initialize_database(connection)
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "old_revision")
                self.assertEqual(dict(connection.execute("SELECT * FROM agent_predictions").fetchone()), before)
                self.assertEqual(before["prediction_source"], "historical_backtest")
            finally:
                connection.close()

    # A later policy cannot rewrite the first committed live batch.
    def test_postgresql_preserves_first_live_batch_when_policy_changes(self) -> None:
        with temporary_database_url() as database_url:
            repository = FirstBoardRepository(database_url)
            first = historical_prediction().model_copy(update={
                "prediction_id": "original-live", "prediction_source": "live",
                "scoring_version": "policy-v1",
            })
            later = first.model_copy(update={
                "prediction_id": "rewritten-live", "symbol": "002002", "score": 90,
                "scoring_version": "policy-v2", "created_at": first.created_at + timedelta(hours=1),
            })
            self.assertEqual(repository.upsert_predictions([first]), 1)
            connection = connect(database_url)
            try:
                original = dict(connection.execute("SELECT * FROM agent_live_prediction_snapshots").fetchone())
                self.assertEqual(repository.upsert_predictions([later]), 0)
                initialize_database(connection)
                self.assertEqual(dict(connection.execute("SELECT * FROM agent_live_prediction_snapshots").fetchone()), original)
                ids = [row[0] for row in connection.execute("SELECT prediction_id FROM agent_predictions WHERE prediction_source='live'")]
                self.assertEqual(ids, ["original-live"])
                self.assertEqual(original["scoring_version"], "policy-v1")
                self.assertEqual(original["prediction_count"], 1)
            finally:
                connection.close()

    # Regression scenario: build first board features.
    def test_build_first_board_features(self) -> None:
        features = build_first_board_features(SAMPLE_EVENTS, trade_date=date(2026, 5, 15))

        self.assertEqual([feature.symbol for feature in features], ["301489"])
        feature = features[0]
        self.assertEqual(feature.first_limit_bucket, "early")
        self.assertEqual(feature.turnover_bucket, "extreme")
        self.assertEqual(feature.amount_bucket, "medium")
        self.assertEqual(feature.market_failed_rate_bucket, "fragile")
        self.assertEqual(feature.feature_version, "first-board-feature-v3-no-st-bj-context")

    # Runtime readiness leaves records and Alembic-managed columns unchanged.
    def test_schema_readiness_preserves_feature_records_and_current_columns(self) -> None:
        features = build_first_board_features(SAMPLE_EVENTS, trade_date=date(2026, 5, 15))
        with temporary_database_url() as database_url:
            repository = FirstBoardRepository(database_url)
            repository.upsert_features(features)
            connection = connect(database_url)
            try:
                before = [dict(row) for row in connection.execute("SELECT * FROM first_board_features ORDER BY trade_date,symbol")]
                initialize_database(connection)
                columns = {row[0] for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='first_board_features'"
                )}
                after = [dict(row) for row in connection.execute("SELECT * FROM first_board_features ORDER BY trade_date,symbol")]
            finally:
                connection.close()
            self.assertNotIn("market_sentiment", columns)
            self.assertEqual(after, before)
            self.assertEqual(repository.list_features_for_date(date(2026, 5, 15)), features)

    # Regression scenario: repository upserts and lists features.
    def test_repository_upserts_and_lists_features(self) -> None:
        features = build_first_board_features(SAMPLE_EVENTS, trade_date=date(2026, 5, 15))

        with temporary_database_url() as database_url:
            repository = FirstBoardRepository(database_url=database_url)
            repository.upsert_features(features)
            persisted = repository.list_features_for_date(date(2026, 5, 15))

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].symbol, "301489")

    # Regression scenario: build first board outcome from event performance.
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

    # Regression scenario: outcome does not shift forward when base bar is missing.
    def test_outcome_does_not_shift_forward_when_base_bar_is_missing(self) -> None:
        event = next(item for item in SAMPLE_EVENTS if item.symbol == "301489")
        bars = [
            StockDailyBar(
                symbol=event.symbol,
                trade_date=event.trade_date + timedelta(days=index),
                open=10 + index,
                high=11 + index,
                low=9 + index,
                close=10.5 + index,
                volume=1_000_000,
                amount=10_000_000,
                source="test",
                created_at=datetime.now(timezone.utc),
            )
            for index in range(1, 4)
        ]

        outcome = build_first_board_outcome(
            event=event,
            bars=bars,
            future_events=SAMPLE_EVENTS,
        )

        self.assertFalse(outcome.next_day_ready)
        self.assertFalse(outcome.three_day_ready)
        self.assertFalse(outcome.outcome_ready)
        self.assertIsNone(outcome.next_open_to_close_pct)


if __name__ == "__main__":
    unittest.main()
