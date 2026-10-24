"""Historical candidate dates and later observations are independent contracts."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agents.review_agent import build_review_agent_report
from app.agents.review_horizons import build_horizon_coverage
from app.agents.review_narrative import authoritative_review_facts
from app.agents.runner.gateway import BusinessToolGateway
from app.agents.tools import AgentToolRegistry
from app.models import AgentPrediction, StockDailyBar
from app.repositories import FirstBoardRepository
from app.services.evaluation_agent import _evaluate_prediction
from app.services.llm_provider import DisabledLLMProvider
from app.services.prediction_time import close_provenance
from app.services.promotion_calendar import PromotionCalendar
from app.services.sample_data import SAMPLE_EVENTS


DAYS = [date(2026, 10, day) for day in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]


@pytest.fixture
def cohort(monkeypatch):
    predictions, observed_bars, events = [], [], []
    for batch_index, day in enumerate(DAYS[:6]):
        for index in range(10):
            symbol = f"60{batch_index:02d}{index:02d}"
            created = datetime(day.year, day.month, day.day, 8, tzinfo=timezone.utc)
            predictions.append(AgentPrediction(
                prediction_id=f"{day}-{symbol}", trade_date=day, symbol=symbol, name="测试候选",
                score=90 - index, rating="A", confidence=0.8, scoring_version="scope-test",
                prediction_source="live", data_as_of=day, facts_json={},
                prediction_provenance=close_provenance(), reasons=[], risks=[], created_at=created,
            ))
            for offset, bar_day in enumerate(DAYS[batch_index:]):
                close = 10 + offset * 0.2
                opened = close + (-0.1 if index < 4 else 0.1 if index < 8 else 0)
                observed_bars.append(StockDailyBar(
                    symbol=symbol, trade_date=bar_day, open=opened, high=close + 0.3,
                    low=close - 0.3, close=close, volume=1_000_000,
                    amount=10_000_000, source="test", created_at=created,
                ))
            events.append(SAMPLE_EVENTS[2].model_copy(update={
                "trade_date": day, "symbol": symbol, "board_height": 1, "closed_limit": True,
            }))
            events.append(SAMPLE_EVENTS[2].model_copy(update={
                "trade_date": DAYS[batch_index + 1], "symbol": symbol,
                "board_height": 2, "closed_limit": True,
            }))
    # Market date presence is independent of a candidate's K-line observations.
    events.append(SAMPLE_EVENTS[2].model_copy(update={"trade_date": DAYS[-1]}))
    repo = Mock(spec=FirstBoardRepository)
    repo.list_predictions_between.side_effect = lambda start, end: [
        item for item in predictions if start <= item.trade_date <= end
    ]
    repo.list_post_bars.side_effect = lambda symbol, base, limit: [
        item for item in observed_bars if item.symbol == symbol and item.trade_date >= base
    ][:limit]
    repo.list_daily_bars_for_symbols.side_effect = lambda symbols, **kwargs: [
        item for item in observed_bars if item.symbol in symbols and item.trade_date <= kwargs["end_date"]
    ]
    monkeypatch.setattr("app.agents.review_agent.build_agent_evaluation", lambda **kwargs: SimpleNamespace(
        prediction_count=len(repo.list_predictions_between(kwargs["start_date"], kwargs["end_date"])),
        evaluations=[_evaluate_prediction(item, None) for item in
                     repo.list_predictions_between(kwargs["start_date"], kwargs["end_date"])],
    ))
    monkeypatch.setattr("app.agents.review_agent.load_promotion_calendar",
                        lambda *args: PromotionCalendar(tuple(DAYS)))
    monkeypatch.setattr("app.agents.review_agent.get_llm_provider", DisabledLLMProvider)
    monkeypatch.setattr("app.agents.review_agent.build_top10_outcome_completeness",
                        lambda **kwargs: SimpleNamespace(warnings=[]))
    monkeypatch.setattr("app.agents.review_digest.build_digest_leaders", lambda **kwargs: ([], []))
    return SimpleNamespace(repository=repo, events=events, bars=observed_bars)


def report(cohort, **kwargs):
    return build_review_agent_report(
        events=cohort.events, repository=cohort.repository, start_date=DAYS[0], end_date=DAYS[4],
        min_score=0, top_per_day=10, provider=DisabledLLMProvider(), trade_dates=DAYS, **kwargs,
    )


def test_october_cohort_keeps_all_five_batches_and_observes_d5_through_october_23(cohort):
    result = report(cohort, as_of_date=DAYS[-1])
    assert (result.start_date, result.end_date, result.as_of_date) == (DAYS[0], DAYS[4], DAYS[-1])
    assert result.sample_size == 50  # October 19 batch must not enter the fixed cohort.
    assert result.review_digest.candidate_dates == DAYS[:5]
    assert result.review_digest.overview.candidate_count == 50
    assert result.review_digest.as_of_date == DAYS[-1]
    assert result.top_pick_promotion_sample_size == 50
    for horizon in ("D+1", "D+3", "D+5"):
        stats = result.horizon_coverage["horizons"][horizon]
        assert (stats["mature_count"], stats["available_count"]) == (50, 50)
        assert (stats["missing_count"], stats["not_mature_count"]) == (0, 0)
        assert stats["positive_count"] == 50
    assert result.horizon_coverage["horizons"]["D+5"]["mean_close_return_pct"] == pytest.approx(10)
    last_batch = [pick for pick in result.reviewed_picks if pick.trade_date == DAYS[4]]
    assert len(last_batch) == 10
    assert all([bar.trading_day_offset for bar in pick.post_bars] == list(range(6)) for pick in last_batch)
    assert all(pick.post_bars[-1].trade_date == DAYS[-1] and pick.outcome_ready for pick in last_batch)
    cohort.repository.upsert_predictions.assert_not_called()
    cohort.repository.upsert_outcomes.assert_not_called()


def test_omitted_business_cutoff_preserves_historical_snapshot_behavior(cohort):
    result = report(cohort)
    assert result.as_of_date == DAYS[4]
    assert result.pending_count == 10
    assert result.review_digest.candidate_dates == DAYS[:4]
    assert result.review_digest.overview.candidate_count == 40
    assert result.horizon_coverage["horizons"]["D+1"]["available_count"] == 40
    assert result.horizon_coverage["horizons"]["D+3"]["available_count"] == 20
    assert result.horizon_coverage["horizons"]["D+5"]["not_mature_count"] == 50
    assert all(bar.trade_date <= DAYS[4] for pick in result.reviewed_picks for bar in pick.post_bars)


def test_explicit_old_cutoff_keeps_requested_end_date_batch_and_reports_it_immature(cohort):
    result = report(cohort, as_of_date=DAYS[4])
    assert result.review_digest.candidate_dates == DAYS[:5]
    assert result.review_digest.overview.candidate_count == 50
    assert result.pending_count == 10
    assert result.horizon_coverage["horizons"]["D+1"]["not_mature_count"] == 10


def test_missing_exact_d5_is_not_called_immature_or_replaced_with_d4(cohort):
    cohort.bars[:] = [bar for bar in cohort.bars if not (bar.symbol == "600400" and bar.trade_date == DAYS[-1])]
    stats = report(cohort, as_of_date=DAYS[-1]).horizon_coverage["horizons"]["D+5"]
    assert (stats["mature_count"], stats["available_count"], stats["missing_count"]) == (50, 49, 1)
    assert stats["not_mature_count"] == 0


def test_source_time_and_three_return_groupings_remain_independently_named(cohort):
    result = report(cohort, as_of_date=DAYS[-1])
    assert result.prediction_source_counts == {"live": 50}
    assert result.time_cohort_counts == {"close_baseline": 50}
    assert result.evaluation_label_counts == {"success": 20, "miss": 20, "partial": 10}
    assert (result.success_count, result.failed_count, result.partial_count) == (20, 20, 10)
    assert result.feature_summary.positive_count == 50
    assert result.feature_summary.negative_count == 0
    assert result.review_digest.overview.excellent_count == 50
    assert "不能" in result.grouping_definitions["prediction_source"]
    assert "次日开盘到收盘" in result.grouping_definitions["evaluation_labels"]
    assert "最多D+5" in result.grouping_definitions["feature_return_groups"]
    assert "9.8%" in result.grouping_definitions["digest_groups"]
    authority = authoritative_review_facts(result, result.reviewed_picks, {}, {})
    assert authority["forward_validation"]["eligible_sample_count"] == 0
    assert authority["grouping_definitions"] == result.grouping_definitions
    assert authority["horizon_coverage"] == result.horizon_coverage


@pytest.mark.parametrize("explicit", [False, True])
def test_gateway_uses_latest_only_for_omitted_observation_cutoff_and_echoes_dates(cohort, explicit):
    gateway = BusinessToolGateway(AgentToolRegistry(cohort.events, cohort.repository))
    args = {"start_date": DAYS[0].isoformat(), "end_date": DAYS[4].isoformat()}
    if explicit:
        args["as_of_date"] = DAYS[4].isoformat()
    args = gateway.validate({"name": "review_high_score_picks", "args": args})
    _, payload, state = gateway.execute("review_high_score_picks", args)
    assert state == "ok"
    assert payload["end_date"] == DAYS[4].isoformat()
    assert payload["as_of_date"] == (DAYS[4] if explicit else DAYS[-1]).isoformat()
    assert payload["horizon_coverage"]["horizons"]["D+5"]["available_count"] == (0 if explicit else 50)
    if explicit:
        with pytest.raises(ValueError, match="request requires"):
            gateway._assert_requested_dates("review_high_score_picks", args,
                                            {**payload, "as_of_date": DAYS[-1].isoformat()})


def test_observation_cutoff_cannot_precede_candidate_end(cohort):
    with pytest.raises(ValueError, match="观察截止日"):
        report(cohort, as_of_date=DAYS[3])
    with pytest.raises(ValueError, match="观察截止日"):
        AgentToolRegistry(cohort.events, cohort.repository).review_high_score_picks(
            DAYS[0], DAYS[4], as_of_date=DAYS[3],
        )


@pytest.mark.parametrize("cutoff", [date(2026, 10, 24), date(2026, 10, 26)])
def test_chat_rejects_uncovered_cutoff_instead_of_calling_missing_future_immature(cohort, cutoff):
    with pytest.raises(ValueError, match="最晚可用日期为2026-10-23"):
        AgentToolRegistry(cohort.events, cohort.repository).review_high_score_picks(
            DAYS[0], DAYS[4], as_of_date=cutoff,
        )


def test_unknown_calendar_is_disclosed_separately_from_immaturity(cohort):
    picks = report(cohort, as_of_date=DAYS[-1]).reviewed_picks
    stats = build_horizon_coverage(picks, trade_dates=[], as_of_date=DAYS[-1])["horizons"]["D+5"]
    assert stats["calendar_unknown_count"] == 50
    assert stats["not_mature_count"] == stats["available_count"] == 0
    assert stats["mean_close_return_pct"] is None


@pytest.mark.parametrize("follow_days, horizons", [(1, {"D+1"}), (3, {"D+1", "D+3"})])
def test_shorter_requested_followup_does_not_invent_missing_d5_prices(cohort, follow_days, horizons):
    result = report(cohort, as_of_date=DAYS[-1], follow_days=follow_days)
    assert set(result.horizon_coverage["horizons"]) == horizons
    assert result.horizon_coverage["requested_follow_days"] == follow_days
    assert f"最多D+{follow_days}" in result.grouping_definitions["feature_return_groups"]


def test_missing_calendar_does_not_remove_saved_candidates_from_digest(cohort):
    result = build_review_agent_report(
        events=cohort.events, repository=cohort.repository, start_date=DAYS[0], end_date=DAYS[4],
        as_of_date=DAYS[-1], min_score=0, provider=DisabledLLMProvider(), trade_dates=[],
    )
    assert result.sample_size == result.review_digest.overview.candidate_count == 50
    assert result.review_digest.candidate_dates == DAYS[:5]
    assert result.review_digest.overview.unobserved_count == 50


def test_daily_mean_groups_distinguish_multiple_positive_batches_flat_and_missing():
    picks = [SimpleNamespace(trade_date=DAYS[index], post_bars=[SimpleNamespace(
        trade_date=DAYS[index + 1], trading_day_offset=1, return_from_base_pct=value,
    )]) for index, value in enumerate((1.86, -6.18, 8.10, 0, None))]
    coverage = build_horizon_coverage(picks, trade_dates=DAYS, as_of_date=DAYS[-1])
    groups = coverage["daily_mean_groups"]["D+1"]
    assert groups == {
        "cohort_count": 5,
        "positive_mean_dates": [DAYS[0].isoformat(), DAYS[2].isoformat()],
        "negative_mean_dates": [DAYS[1].isoformat()],
        "flat_mean_dates": [DAYS[3].isoformat()],
        "unavailable_mean_dates": [DAYS[4].isoformat()],
    }
