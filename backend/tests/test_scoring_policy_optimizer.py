import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.agents.first_board import build_first_board_ratings
from app.models import FirstBoardOutcome, LimitUpEvent, ScoringPolicy
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.sample_data import SAMPLE_EVENTS
from app.services.scoring_policy import build_default_scoring_policy
from app.services.scoring_policy_optimizer import (
    TARGET_WEIGHTS,
    evaluate_scoring_policy,
    optimize_scoring_policy,
)


class ScoringPolicyOptimizerTest(unittest.TestCase):
    def test_default_policy_exposes_explicit_board_shape_and_market_cap_weights(self) -> None:
        policy = build_default_scoring_policy()

        response = build_first_board_ratings(
            SAMPLE_EVENTS,
            scoring_policy=policy,
        )
        rating = response.candidates[0]

        self.assertEqual(response.generated_by, policy.version)
        self.assertEqual(policy.factor_weights["board_pattern"], 10)
        self.assertEqual(policy.factor_weights["market_cap_preference"], 10)
        self.assertEqual(sum(policy.factor_weights.values()), 100)
        self.assertEqual(
            [item.max_score for item in rating.score_breakdown],
            list(policy.factor_weights.values()),
        )

    def test_explicit_challenger_changes_factor_contribution(self) -> None:
        baseline = build_default_scoring_policy()
        weights = dict(baseline.factor_weights)
        weights["first_limit_time"] -= 2
        weights["amount"] += 2
        challenger = ScoringPolicy(
            version="test-challenger",
            parent_version=baseline.version,
            status="challenger",
            factor_weights=weights,
            source="manual",
            created_at=datetime.now(timezone.utc),
        )

        baseline_score = build_first_board_ratings(
            SAMPLE_EVENTS,
            scoring_policy=baseline,
        ).candidates[0].score
        challenger_response = build_first_board_ratings(
            SAMPLE_EVENTS,
            scoring_policy=challenger,
        )

        self.assertEqual(challenger_response.generated_by, "test-challenger")
        self.assertNotEqual(challenger_response.candidates[0].score, baseline_score)

    def test_legacy_default_champion_migrates_to_current_default(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"scoring-policy-migration-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database, database_path)
        repository = SQLiteScoringPolicyRepository(database_path)
        current = build_default_scoring_policy()
        legacy = current.model_copy(
            update={
                "version": "first-board-rule-v2-enriched",
                "parent_version": None,
                "source": "default",
                "created_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
                "activated_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
            }
        )
        repository.upsert_policy(legacy)

        migrated = repository.ensure_default_policy()

        self.assertEqual(migrated.version, current.version)
        self.assertEqual(repository.get_champion().version, current.version)
        self.assertEqual(
            repository.get_policy(legacy.version).status,
            "archived",
        )

    def test_walk_forward_registers_but_does_not_promote_thin_sample(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"scoring-policy-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database, database_path)
        first_board_repository = SQLiteFirstBoardRepository(database_path)
        policy_repository = SQLiteScoringPolicyRepository(database_path)
        events, outcomes = self._history(days=14)
        first_board_repository.upsert_outcomes(outcomes)

        report = optimize_scoring_policy(
            events=events,
            start_date=events[0].trade_date,
            end_date=events[-1].trade_date,
            first_board_repository=first_board_repository,
            policy_repository=policy_repository,
            top_k=3,
            minimum_trade_dates=8,
            activate_if_eligible=True,
            now=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        self.assertFalse(report.comparison.promotion_eligible)
        self.assertFalse(report.activated)
        self.assertEqual(len(report.train_dates), 9)
        self.assertEqual(len(report.validation_dates), 2)
        self.assertEqual(len(report.test_dates), 2)
        self.assertTrue(report.challenger_policy.version.startswith("first-board-rule-v3-"))
        self.assertEqual(
            set(report.target_correlations),
            {
                "next_open_to_close",
                "promotion",
                "downside_protection",
                "composite",
            },
        )
        self.assertGreaterEqual(len(report.walk_forward_folds), 1)
        tested_dates = [
            trade_date
            for fold in report.walk_forward_folds
            for trade_date in fold.test_dates
        ]
        self.assertEqual(len(tested_dates), len(set(tested_dates)))
        self.assertIn(
            "晋级标签完整交易日只有 14",
            " ".join(report.comparison.gate_reasons),
        )
        self.assertEqual(
            policy_repository.get_champion().version,
            build_default_scoring_policy().version,
        )
        self.assertIsNotNone(
            policy_repository.get_policy(report.challenger_policy.version)
        )
        self.assertEqual(
            policy_repository.get_latest_optimization_run().run_id,
            report.run_id,
        )

    def test_policy_metrics_make_promotion_lift_explicit(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"scoring-policy-metrics-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database, database_path)
        repository = SQLiteFirstBoardRepository(database_path)
        events, outcomes = self._history(days=1)
        repository.upsert_outcomes(outcomes)
        outcome_map = {
            (item.base_trade_date, item.symbol): item for item in outcomes
        }

        metrics = evaluate_scoring_policy(
            events=events,
            dates=sorted({item.trade_date for item in events}),
            outcomes=outcome_map,
            policy=build_default_scoring_policy(),
            repository=repository,
            top_k=1,
        )

        self.assertEqual(TARGET_WEIGHTS["promotion"], 0.60)
        self.assertEqual(metrics.promoted_to_second_board_rate, 1.0)
        self.assertEqual(metrics.pool_promoted_to_second_board_rate, 0.25)
        self.assertEqual(metrics.promotion_rate_lift, 0.75)
        self.assertIsNotNone(metrics.objective_score)

    def test_promotion_metrics_do_not_depend_on_return_cache_coverage(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"scoring-policy-event-labels-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database, database_path)
        repository = SQLiteFirstBoardRepository(database_path)
        events, outcomes = self._history(days=1)
        base_date = events[0].trade_date
        events.append(
            events[0].model_copy(
                update={
                    "trade_date": base_date + timedelta(days=1),
                    "board_height": 2,
                }
            )
        )
        only_cached_outcome = outcomes[0]

        metrics = evaluate_scoring_policy(
            events=events,
            dates=[base_date],
            outcomes={(base_date, only_cached_outcome.symbol): only_cached_outcome},
            policy=build_default_scoring_policy(),
            repository=repository,
            top_k=1,
        )

        self.assertEqual(metrics.trade_date_count, 1)
        self.assertEqual(metrics.pool_sample_size, 4)
        self.assertEqual(metrics.top_sample_size, 1)
        self.assertEqual(metrics.pool_return_sample_size, 1)
        self.assertEqual(metrics.top_return_sample_size, 1)
        self.assertEqual(metrics.promoted_to_second_board_rate, 1.0)
        self.assertEqual(metrics.pool_promoted_to_second_board_rate, 0.25)

    @staticmethod
    def _cleanup_database(database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    @staticmethod
    def _history(days: int) -> tuple[list[LimitUpEvent], list[FirstBoardOutcome]]:
        events: list[LimitUpEvent] = []
        outcomes: list[FirstBoardOutcome] = []
        started = date(2026, 7, 1)
        now = datetime(2026, 8, 19, tzinfo=timezone.utc)
        first_limit_times = (time(9, 35), time(9, 55), time(10, 15), time(10, 35))
        for day_index in range(days):
            trade_date = started + timedelta(days=day_index * 7)
            for stock_index in range(4):
                symbol = f"00{day_index:02d}{stock_index:02d}"
                events.append(
                    LimitUpEvent(
                        symbol=symbol,
                        name=f"测试{day_index}-{stock_index}",
                        trade_date=trade_date,
                        first_limit_time=first_limit_times[stock_index],
                        last_limit_time=time(10, 0),
                        seal_count=stock_index + 1,
                        break_count=stock_index,
                        closed_limit=True,
                        board_height=1,
                        amount=300_000_000 + stock_index * 200_000_000,
                        turnover_rate=5 + stock_index * 4,
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
                next_return = 2.0 - stock_index * 1.5
                outcomes.append(
                    FirstBoardOutcome(
                        base_trade_date=trade_date,
                        symbol=symbol,
                        next_trade_date=trade_date + timedelta(days=1),
                        next_open_pct=0,
                        next_high_pct=max(next_return, 0),
                        next_close_pct=next_return,
                        next_open_to_high_pct=max(next_return, 0),
                        next_open_to_low_pct=min(next_return, 0),
                        next_open_to_close_pct=next_return,
                        three_day_high_pct=next_return + 1,
                        three_day_close_pct=next_return,
                        max_drawdown_3d=min(next_return, 0),
                        three_day_open_to_high_pct=next_return + 1,
                        three_day_open_to_close_pct=next_return,
                        max_drawdown_from_next_open_3d=min(next_return, 0),
                        promoted_to_second_board=stock_index == 0,
                        next_day_ready=True,
                        three_day_ready=True,
                        outcome_ready=True,
                        outcome_version="test-v1",
                        created_at=now,
                    )
                )
        return events, outcomes


if __name__ == "__main__":
    unittest.main()
