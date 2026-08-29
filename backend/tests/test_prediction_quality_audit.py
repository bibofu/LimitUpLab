import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import AgentPrediction, FirstBoardOutcome, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.prediction_quality_audit import build_prediction_quality_audit
from app.services.scoring_policy import build_default_scoring_policy


class PredictionQualityAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"prediction-quality-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.policy_repository = SQLiteScoringPolicyRepository(self.database_path)
        self.policy = build_default_scoring_policy()
        self.policy_repository.upsert_policy(self.policy)
        self.events = self._events(days=4, stocks=3)

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_audit_separates_cohorts_and_reports_maturity(self) -> None:
        predictions = self._predictions()
        predictions.append(
            predictions[0].model_copy(
                update={
                    "prediction_id": "live-duplicate",
                    "prediction_source": "live",
                    "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                }
            )
        )
        predictions.append(
            predictions[0].model_copy(
                update={
                    "prediction_id": "v1-duplicate",
                    "scoring_version": "first-board-rule-v1",
                    "data_as_of": predictions[0].trade_date + timedelta(days=1),
                }
            )
        )
        self.repository.upsert_predictions(predictions)
        self.repository.upsert_outcomes(self._outcomes())

        report = build_prediction_quality_audit(
            events=self.events,
            start_date=self.events[0].trade_date,
            end_date=self.events[-1].trade_date,
            first_board_repository=self.repository,
            policy_repository=self.policy_repository,
            top_k=3,
        )

        self.assertEqual(report.raw_prediction_rows, 14)
        self.assertEqual(report.audited_prediction_rows, 13)
        self.assertEqual(report.canonical_prediction_count, 10)
        self.assertEqual(report.cross_cohort_duplicate_rows, 2)
        self.assertEqual(report.data_as_of_violation_count, 1)
        self.assertEqual(report.next_day_mature_trade_date_count, 3)
        self.assertEqual(report.complete_next_day_trade_date_count, 1)
        self.assertEqual(
            [item.status for item in report.date_coverage],
            ["complete", "partial", "pending", "not_mature"],
        )
        self.assertEqual(report.benchmarks[0].benchmark, "audited_policy_top_k")
        self.assertEqual(report.benchmarks[0].sample_size, 1)
        self.assertEqual(report.policy_status.outcome_ready_trade_dates, 2)
        self.assertFalse(report.policy_status.readiness_rate >= 1)

    def _predictions(self) -> list[AgentPrediction]:
        created_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        predictions: list[AgentPrediction] = []
        for event in self.events:
            predictions.append(
                AgentPrediction(
                    prediction_id=f"prediction-{event.trade_date}-{event.symbol}",
                    trade_date=event.trade_date,
                    symbol=event.symbol,
                    name=event.name,
                    score=90 - int(event.symbol[-1]) * 10,
                    rating="A",
                    confidence=0.8,
                    scoring_version=self.policy.version,
                    prediction_source="historical_backtest",
                    data_as_of=event.trade_date,
                    facts_json={},
                    reasons=[],
                    risks=[],
                    created_at=created_at,
                )
            )
        return predictions

    def _outcomes(self) -> list[FirstBoardOutcome]:
        outcomes: list[FirstBoardOutcome] = []
        started = self.events[0].trade_date
        created_at = datetime(2026, 7, 8, tzinfo=timezone.utc)
        for day_index, ready_count in ((0, 3), (1, 2)):
            trade_date = started + timedelta(days=day_index)
            for stock_index in range(ready_count):
                value = 2.0 - stock_index
                outcomes.append(
                    FirstBoardOutcome(
                        base_trade_date=trade_date,
                        symbol=f"00{day_index:02d}{stock_index:02d}",
                        next_trade_date=trade_date + timedelta(days=1),
                        next_open_to_close_pct=value,
                        next_open_to_low_pct=-1.0,
                        three_day_open_to_close_pct=value + 0.5,
                        max_drawdown_from_next_open_3d=-1.5,
                        promoted_to_second_board=stock_index == 0,
                        next_day_ready=True,
                        three_day_ready=day_index == 0,
                        outcome_ready=True,
                        outcome_version="test-v1",
                        created_at=created_at,
                    )
                )
        return outcomes

    @staticmethod
    def _events(days: int, stocks: int) -> list[LimitUpEvent]:
        events: list[LimitUpEvent] = []
        started = date(2026, 7, 1)
        for day_index in range(days):
            trade_date = started + timedelta(days=day_index)
            for stock_index in range(stocks):
                events.append(
                    LimitUpEvent(
                        symbol=f"00{day_index:02d}{stock_index:02d}",
                        name=f"测试{day_index}-{stock_index}",
                        trade_date=trade_date,
                        first_limit_time=time(9, 35 + stock_index * 5),
                        last_limit_time=time(10, 0),
                        seal_count=1,
                        break_count=stock_index,
                        closed_limit=True,
                        board_height=1,
                        amount=500_000_000,
                        turnover_rate=8,
                        industry="测试行业",
                        concept="测试题材",
                        next_open_pct=0,
                        next_high_pct=0,
                        next_close_pct=0,
                        three_day_return_pct=0,
                        five_day_return_pct=0,
                        continued_next_day=False,
                    )
                )
        return events


if __name__ == "__main__":
    unittest.main()
