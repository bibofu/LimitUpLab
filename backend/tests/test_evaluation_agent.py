import os
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

from app.models import AgentPrediction, FirstBoardOutcome, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.evaluation_agent import (
    build_agent_evaluation,
    persist_agent_predictions_for_dates,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class EvaluationAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def _database_path(self) -> Path:
        return TEST_TMP_ROOT / f"evaluation-agent-{uuid4().hex}.sqlite"

    def _cleanup_database(self, database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def _event(
        self,
        symbol: str,
        name: str,
        trade_date: date,
        first_limit_time: time,
        break_count: int,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            first_limit_time=first_limit_time,
            last_limit_time=first_limit_time,
            seal_count=1,
            break_count=break_count,
            closed_limit=True,
            board_height=1,
            amount=350_000_000,
            turnover_rate=6.0,
            industry="\u5143\u4ef6",
            concept="\u667a\u80fd\u7535\u7f51",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def _make_outcome(
        self,
        symbol: str,
        base_trade_date: date,
        next_high_pct: float,
        next_close_pct: float,
        three_day_high_pct: float,
        three_day_close_pct: float,
        promoted: bool,
    ) -> FirstBoardOutcome:
        return FirstBoardOutcome(
            base_trade_date=base_trade_date,
            symbol=symbol,
            next_trade_date=date(2026, 8, 11),
            next_open_pct=1.0,
            next_high_pct=next_high_pct,
            next_close_pct=next_close_pct,
            next_open_to_high_pct=next_high_pct - 1.0,
            next_open_to_low_pct=-3.0,
            next_open_to_close_pct=next_close_pct,
            three_day_high_pct=three_day_high_pct,
            three_day_close_pct=three_day_close_pct,
            max_drawdown_3d=-3.0,
            three_day_open_to_high_pct=three_day_high_pct - 1.0,
            three_day_open_to_close_pct=three_day_close_pct,
            max_drawdown_from_next_open_3d=-4.0,
            promoted_to_second_board=promoted,
            next_day_ready=True,
            three_day_ready=True,
            outcome_ready=True,
            outcome_version="test",
            created_at=datetime.now(timezone.utc),
        )

    def test_evaluation_agent_reads_immutable_predictions_and_labels_entry_returns(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 10)
            events = [
                self._event("002001", "\u6210\u529f\u6837\u672c", trade_date, time(9, 35), 0),
                self._event("002002", "\u8bef\u5224\u6837\u672c", trade_date, time(9, 38), 0),
                self._event("002003", "\u6f0f\u5224\u6837\u672c", trade_date, time(14, 20), 3),
            ]
            repository.upsert_outcomes(
                [
                    self._make_outcome("002001", trade_date, 7.0, 2.0, 10.0, 5.0, True),
                    self._make_outcome("002002", trade_date, 1.0, -2.0, 2.0, -5.0, False),
                    self._make_outcome("002003", trade_date, 9.0, 1.0, 12.0, 6.0, True),
                ]
            )

            empty_response = build_agent_evaluation(
                events=events,
                start_date=trade_date,
                end_date=trade_date,
                first_board_repository=repository,
            )
            self.assertEqual(empty_response.prediction_count, 0)
            persist_agent_predictions_for_dates(
                events=events,
                trade_dates=[trade_date],
                repository=repository,
                prediction_source="live",
                data_as_of=trade_date,
            )

            response = build_agent_evaluation(
                events=events,
                start_date=trade_date,
                end_date=trade_date,
                first_board_repository=repository,
            )

            self.assertEqual(response.prediction_count, 3)
            self.assertEqual(response.outcome_ready_count, 3)
            self.assertEqual(response.source_counts, {"live": 3})
            labels = {item.symbol: item.evaluation_label for item in response.evaluations}
            self.assertEqual(labels["002001"], "success")
            self.assertEqual(labels["002002"], "miss")
            self.assertEqual(labels["002003"], "false_negative")
            self.assertTrue(response.summary)
            self.assertTrue(all(item.prediction_source == "live" for item in response.evaluations))
            self.assertEqual(len(repository.list_predictions_between(trade_date, trade_date)), 3)
        finally:
            self._cleanup_database(database_path)

    def test_evaluation_keeps_live_predictions_visible_after_policy_upgrade(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 25)
            created_at = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
            repository.upsert_predictions(
                [
                    AgentPrediction(
                        prediction_id="legacy-live-prediction",
                        trade_date=trade_date,
                        symbol="002001",
                        name="历史实时预测",
                        score=82,
                        rating="A",
                        confidence=0.8,
                        scoring_version="first-board-rule-v2-enriched",
                        prediction_source="live",
                        data_as_of=trade_date,
                        facts_json={},
                        reasons=["升级前保存的实时预测"],
                        risks=[],
                        created_at=created_at,
                    ),
                    AgentPrediction(
                        prediction_id="current-backtest-prediction",
                        trade_date=trade_date,
                        symbol="002001",
                        name="当前版本回测",
                        score=90,
                        rating="A",
                        confidence=0.9,
                        scoring_version="first-board-rule-v3-board-shape-small-cap",
                        prediction_source="historical_backtest",
                        data_as_of=trade_date,
                        facts_json={},
                        reasons=["升级后生成的历史回测"],
                        risks=[],
                        created_at=datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc),
                    ),
                ]
            )

            response = build_agent_evaluation(
                events=[],
                start_date=trade_date,
                end_date=trade_date,
                first_board_repository=repository,
            )

            self.assertEqual(response.prediction_count, 1)
            self.assertEqual(response.source_counts, {"live": 1})
            self.assertEqual(response.evaluations[0].prediction_id, "legacy-live-prediction")
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
