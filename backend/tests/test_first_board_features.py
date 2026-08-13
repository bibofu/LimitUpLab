import os
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.database import connect, initialize_database
from app.repositories import SQLiteFirstBoardRepository
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)
from app.services.similar_cases import (
    calculate_similarity,
    find_similar_first_board_cases,
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

    def test_repository_recalls_similar_features(self) -> None:
        all_features = []
        for trade_date in (date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)):
            all_features.extend(build_first_board_features(SAMPLE_EVENTS, trade_date=trade_date))

        with temporary_database_path() as database_path:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            repository.upsert_features(all_features)
            target = repository.get_feature("301489", date(2026, 5, 15))
            self.assertIsNotNone(target)
            recalled = repository.recall_similar_features(
                target=target,
                earliest_trade_date=date(2026, 5, 13),
                limit=10,
            )

        self.assertTrue(any(feature.symbol == "300124" for feature in recalled))

    def test_calculate_similarity_is_explainable(self) -> None:
        features = []
        for trade_date in (date(2026, 5, 13), date(2026, 5, 15)):
            features.extend(build_first_board_features(SAMPLE_EVENTS, trade_date=trade_date))
        target = next(feature for feature in features if feature.symbol == "301489")
        candidate = next(feature for feature in features if feature.symbol == "300124")

        result = calculate_similarity(target, candidate)

        self.assertGreater(result.score, 0)
        self.assertTrue(result.reasons or result.differences)

    def test_find_similar_first_board_cases(self) -> None:
        all_features = []
        for trade_date in (date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)):
            all_features.extend(build_first_board_features(SAMPLE_EVENTS, trade_date=trade_date))

        with temporary_database_path() as database_path:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            repository.upsert_features(all_features)
            response = find_similar_first_board_cases(
                symbol="301489",
                trade_date=date(2026, 5, 15),
                repository=repository,
                limit=2,
                window_days=60,
            )

        self.assertEqual(response.target.symbol, "301489")
        self.assertLessEqual(len(response.cases), 2)
        self.assertGreater(response.recall_count, 0)

    def test_build_first_board_outcome_from_event_performance(self) -> None:
        event = next(item for item in SAMPLE_EVENTS if item.symbol == "301489")
        outcome = build_first_board_outcome(
            event=event,
            bars=[],
            future_events=SAMPLE_EVENTS,
        )

        self.assertFalse(outcome.outcome_ready)
        self.assertTrue(outcome.promoted_to_second_board)
        self.assertEqual(outcome.outcome_version, "first-board-outcome-v1")


if __name__ == "__main__":
    unittest.main()
