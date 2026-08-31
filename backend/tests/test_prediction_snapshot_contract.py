import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.tools import AgentToolRegistry
from app.models import AgentPrediction
from app.repositories import SQLiteFirstBoardRepository
from app.routers.agents import get_first_board_ratings
from app.services.evaluation_agent import (
    persist_agent_predictions_for_dates,
    select_canonical_prediction_snapshots,
)
from app.services.sample_data import SAMPLE_EVENTS


class PredictionSnapshotContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"prediction-snapshot-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.trade_date = max(item.trade_date for item in SAMPLE_EVENTS)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_repeated_live_generation_is_idempotent(self) -> None:
        first_count = self._persist_live()
        second_count = self._persist_live()

        snapshot = self.repository.get_live_prediction_snapshot(self.trade_date)
        predictions = self.repository.list_predictions_between(
            self.trade_date,
            self.trade_date,
        )
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(len(predictions), 1)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.snapshot_source, "live")
        self.assertEqual(snapshot.data_as_of, self.trade_date)
        self.assertTrue(snapshot.candidates[0].score_breakdown)

    def test_later_scoring_version_cannot_replace_live_snapshot(self) -> None:
        self._persist_live()
        original = self.repository.get_live_prediction_snapshot(self.trade_date)
        stored = self.repository.list_predictions_between(
            self.trade_date,
            self.trade_date,
        )[0]
        assert original is not None
        changed_rating = original.candidates[0].model_copy(
            update={"score": original.candidates[0].score + 5}
        )
        changed = original.model_copy(
            update={
                "candidates": [changed_rating],
                "generated_by": "future-policy-v99",
            }
        )
        changed_at = stored.created_at + timedelta(hours=1)
        changed_prediction = stored.model_copy(
            update={
                "prediction_id": "future-live-prediction",
                "score": changed_rating.score,
                "scoring_version": "future-policy-v99",
                "created_at": changed_at,
            }
        )

        inserted = self.repository.persist_live_prediction_snapshot(
            ratings=changed,
            predictions=[changed_prediction],
            top_limit=10,
            data_as_of=self.trade_date,
            created_at=changed_at,
        )
        after = self.repository.get_live_prediction_snapshot(self.trade_date)

        self.assertEqual(inserted, 0)
        self.assertEqual(after, original)
        self.assertEqual(
            len(
                self.repository.list_predictions_between(
                    self.trade_date,
                    self.trade_date,
                )
            ),
            1,
        )

    def test_auction_final_can_replace_provisional_live_snapshot(self) -> None:
        self._persist_live()
        original = self.repository.get_live_prediction_snapshot(self.trade_date)
        stored = self.repository.list_predictions_between(
            self.trade_date,
            self.trade_date,
        )[0]
        assert original is not None
        final_rating = original.candidates[0].model_copy(
            update={"score": original.candidates[0].score + 3}
        )
        final_created_at = stored.created_at + timedelta(days=1)
        final_data_as_of = self.trade_date + timedelta(days=1)
        final_version = "auction-final-v1"
        final_snapshot = original.model_copy(
            update={
                "candidates": [final_rating],
                "generated_by": final_version,
            }
        )
        final_prediction = stored.model_copy(
            update={
                "prediction_id": "auction-final-prediction",
                "score": final_rating.score,
                "scoring_version": final_version,
                "data_as_of": final_data_as_of,
                "created_at": final_created_at,
            }
        )

        inserted = self.repository.persist_live_prediction_snapshot(
            ratings=final_snapshot,
            predictions=[final_prediction],
            top_limit=10,
            data_as_of=final_data_as_of,
            created_at=final_created_at,
            replace=True,
        )
        after = self.repository.get_live_prediction_snapshot(self.trade_date)
        rows = self.repository.list_predictions_between(
            self.trade_date,
            self.trade_date,
        )

        self.assertEqual(inserted, 1)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after.generated_by, final_version)
        self.assertEqual(after.data_as_of, final_data_as_of)
        self.assertEqual([item.prediction_id for item in rows], ["auction-final-prediction"])

    def test_post_rollout_draft_is_excluded_from_review(self) -> None:
        trade_date = date(2026, 8, 31)
        created_at = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
        draft = self._prediction(
            "draft-live",
            "000001",
            "close-draft-v1",
            "live",
            created_at,
        ).model_copy(update={"trade_date": trade_date, "data_as_of": trade_date})
        official = self._prediction(
            "auction-final",
            "000002",
            "auction-final-v1",
            "live",
            created_at + timedelta(days=1),
        ).model_copy(
            update={
                "trade_date": trade_date,
                "data_as_of": trade_date + timedelta(days=1),
            }
        )

        self.assertEqual(select_canonical_prediction_snapshots([draft]), [])
        selected = select_canonical_prediction_snapshots([draft, official])
        self.assertEqual([item.prediction_id for item in selected], ["auction-final"])

    def test_live_batch_excludes_same_day_historical_extras(self) -> None:
        created_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
        live = [
            self._prediction("live-a", "000001", "live-v1", "live", created_at),
            self._prediction("live-b", "000002", "live-v1", "live", created_at),
        ]
        historical = [
            self._prediction(
                "history-a",
                "000001",
                "history-v2",
                "historical_backtest",
                created_at + timedelta(days=1),
            ),
            self._prediction(
                "history-c",
                "000003",
                "history-v2",
                "historical_backtest",
                created_at + timedelta(days=1),
            ),
        ]

        selected = select_canonical_prediction_snapshots(live + historical)

        self.assertEqual([item.symbol for item in selected], ["000001", "000002"])
        self.assertTrue(all(item.prediction_source == "live" for item in selected))

    def test_api_and_agent_tool_return_same_live_snapshot(self) -> None:
        self._persist_live()
        tool_result = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
        ).first_board_ratings(self.trade_date)

        with (
            patch("app.routers.agents.SQLiteFirstBoardRepository", return_value=self.repository),
            patch("app.routers.agents.get_limit_up_repository") as limit_repository,
        ):
            limit_repository.return_value.list_events.return_value = SAMPLE_EVENTS
            api_result = get_first_board_ratings(self.trade_date)

        self.assertEqual(tool_result.output, api_result)
        self.assertEqual(api_result.snapshot_source, "live")

    def test_full_pool_api_bypasses_persisted_prediction_top10(self) -> None:
        self._persist_live()

        with (
            patch("app.routers.agents.SQLiteFirstBoardRepository", return_value=self.repository),
            patch("app.routers.agents.get_limit_up_repository") as limit_repository,
        ):
            limit_repository.return_value.list_events.return_value = SAMPLE_EVENTS
            api_result = get_first_board_ratings(self.trade_date, full_pool=True)

        self.assertEqual(api_result.snapshot_source, "calculated")
        self.assertEqual(api_result.trade_date, self.trade_date)

    def _persist_live(self) -> int:
        return persist_agent_predictions_for_dates(
            events=SAMPLE_EVENTS,
            trade_dates=[self.trade_date],
            repository=self.repository,
            top_per_day=10,
            prediction_source="live",
            data_as_of=self.trade_date,
        )

    def _prediction(
        self,
        prediction_id: str,
        symbol: str,
        scoring_version: str,
        source: str,
        created_at: datetime,
    ) -> AgentPrediction:
        return AgentPrediction(
            prediction_id=prediction_id,
            trade_date=self.trade_date,
            symbol=symbol,
            name=symbol,
            score=80,
            rating="A",
            confidence=0.8,
            scoring_version=scoring_version,
            prediction_source=source,
            data_as_of=self.trade_date,
            facts_json={},
            reasons=[],
            risks=[],
            created_at=created_at,
        )


if __name__ == "__main__":
    unittest.main()
