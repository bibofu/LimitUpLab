from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

import pytest

from app.agents.review_agent import (
    REVIEW_AGENT_VERSION,
    _build_promotion_comparisons,
    build_review_agent_report,
)
from app.models import (
    AgentEvaluationItem,
    DailyReviewSnapshot,
    ReviewAgentPick,
    ReviewAgentReportResponse,
    ReviewPromotionComparison,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteReviewSnapshotRepository
from app.routers.agents import get_review_agent_report
from app.services.llm_provider import DisabledLLMProvider
from app.services.promotion_calendar import PromotionCalendar
from app.services.sample_data import SAMPLE_EVENTS


BASE = date(2026, 9, 30)
NEXT = date(2026, 10, 8)


def event(day, height, symbol="000001"):
    return SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "board_height": height, "symbol": symbol, "closed_limit": True,
    })


def pick(day=BASE):
    return ReviewAgentPick(
        trade_date=day, symbol="000001", name="测试样本", score=90, rating="A",
        confidence=0.8, prediction_source="live", data_as_of=day,
        evaluation_label="pending", outcome_ready=False, promoted_to_second_board=False,
    )


def evaluation(day=BASE):
    return AgentEvaluationItem(
        **pick(day).model_dump(include={
            "trade_date", "symbol", "name", "score", "rating", "confidence",
            "prediction_source", "data_as_of", "evaluation_label", "outcome_ready", "promoted_to_second_board",
        }),
        prediction_id="calendar-test", lesson="等待核对", scoring_suggestion="等待完整数据",
    )


def repository():
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_predictions_between.return_value = []
    repo.list_post_bars.return_value = []
    return repo


@pytest.mark.parametrize("base,next_day", [
    (BASE, NEXT),
    (date(2026, 9, 25), date(2026, 9, 28)),
])
def test_calendar_adjacent_holiday_and_weekend_dates_are_ready(base, next_day):
    result, = _build_promotion_comparisons(
        events=[event(base, 1), event(next_day, 2)], picks=[pick(base)],
        end_date=next_day, trade_dates=(base, next_day),
    )
    assert result.outcome_ready is True
    assert result.next_trade_date == next_day
    assert result.top_pick_promotion_rate == result.market_promotion_rate == 1


def test_missing_actual_next_trade_day_cannot_be_skipped():
    base, missing, later = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)
    result, = _build_promotion_comparisons(
        events=[event(base, 1), event(later, 2)], picks=[pick(base)],
        end_date=later, trade_dates=(base, missing, later),
    )
    assert result.next_trade_date == missing
    assert result.outcome_ready is False
    assert result.top_pick_promotion_rate is None
    assert result.market_promotion_rate is None
    assert result.top_pick_promoted_count == result.market_promoted_count == 0


def test_unknown_calendar_is_pending_even_when_two_event_dates_exist():
    result, = _build_promotion_comparisons(
        events=[event(BASE, 1), event(NEXT, 2)], picks=[pick()], end_date=NEXT,
    )
    assert result.outcome_ready is False
    assert result.next_trade_date is None
    assert result.top_pick_promotion_rate is None


@pytest.mark.parametrize("events,end", [
    ([event(NEXT, 2)], NEXT),
    ([event(BASE, 1), event(NEXT, 2)], BASE),
])
def test_missing_base_or_followup_beyond_cutoff_is_pending(events, end):
    result, = _build_promotion_comparisons(
        events=events, picks=[pick()], end_date=end, trade_dates=(BASE, NEXT),
    )
    assert result.outcome_ready is False
    assert result.top_pick_promotion_rate is None


def test_tool_and_report_share_one_calendar_load_using_observed_endpoints():
    with (
        patch("app.agents.review_agent.load_promotion_calendar", return_value=PromotionCalendar((BASE, NEXT), ())) as load,
        patch("app.agents.review_agent.ReviewAgentToolbox._high_score_evaluations", return_value=[evaluation()]),
    ):
        report = build_review_agent_report(
            events=[event(BASE, 1), event(NEXT, 2)], repository=repository(),
            start_date=date(2026, 9, 26), end_date=date(2026, 10, 11),
            provider=DisabledLLMProvider(),
        )
    load.assert_called_once_with(BASE, NEXT)
    comparison, = report.promotion_comparisons
    assert comparison.outcome_ready is True
    assert report.top_pick_promotion_rate == 1
    tool = next(t for t in report.tool_results if t.name == "compare_top10_market_promotion")
    assert tool.output["daily_comparisons"][0]["next_trade_date"] == NEXT.isoformat()
    assert tool.result.status == "ok"


def test_calendar_failure_is_partial_in_trace_and_retried_for_a_later_report():
    with (
        patch("app.agents.review_agent.load_promotion_calendar", side_effect=[
            PromotionCalendar((), ("交易日历暂不可用",)), PromotionCalendar((BASE, NEXT), ()),
        ]) as load,
        patch("app.agents.review_agent.ReviewAgentToolbox._high_score_evaluations", return_value=[evaluation()]),
    ):
        reports = [build_review_agent_report(
            events=[event(BASE, 1), event(NEXT, 2)], repository=repository(),
            start_date=BASE, end_date=NEXT, provider=DisabledLLMProvider(),
        ) for _ in range(2)]
    assert load.call_count == 2
    failed, recovered = reports
    assert failed.promotion_ready_date_count == 0
    assert failed.top_pick_promotion_rate is None
    assert any("1进2晋级日历：交易日历暂不可用" in warning for warning in failed.warnings)
    assert any("下一交易日" in warning for warning in failed.warnings)
    tool = next(t for t in failed.tool_results if t.name == "compare_top10_market_promotion")
    assert tool.status == "success"
    assert tool.result.status == "partial"
    assert tool.output["complete"] is False
    assert tool.output["data_missing"]
    assert recovered.top_pick_promotion_rate == 1


def test_explicit_empty_calendar_never_loads_or_infers_dates():
    with (
        patch("app.agents.review_agent.load_promotion_calendar") as load,
        patch("app.agents.review_agent.ReviewAgentToolbox._high_score_evaluations", return_value=[evaluation()]),
    ):
        report = build_review_agent_report(
            events=[event(BASE, 1), event(NEXT, 2)], repository=repository(),
            start_date=BASE, end_date=NEXT, provider=DisabledLLMProvider(), trade_dates=(),
        )
    load.assert_not_called()
    assert report.promotion_comparisons[0].outcome_ready is False


def test_report_warning_names_the_missing_next_event_date():
    missing, later = date(2026, 10, 8), date(2026, 10, 9)
    with patch("app.agents.review_agent.ReviewAgentToolbox._high_score_evaluations", return_value=[evaluation()]):
        report = build_review_agent_report(
            events=[event(BASE, 1), event(later, 2)], repository=repository(),
            start_date=BASE, end_date=later, provider=DisabledLLMProvider(),
            trade_dates=(BASE, missing, later),
        )
    assert any("1进2" in warning and "缺少下一交易日 2026-10-08" in warning for warning in report.warnings)
    assert report.promotion_ready_date_count == 0


def test_cutoff_day_pending_is_not_mislabelled_as_calendar_failure():
    with patch("app.agents.review_agent.ReviewAgentToolbox._high_score_evaluations", return_value=[evaluation()]):
        report = build_review_agent_report(
            events=[event(BASE, 1)], repository=repository(),
            start_date=BASE, end_date=BASE, provider=DisabledLLMProvider(),
            trade_dates=(BASE,),
        )
    assert report.promotion_comparisons[0].outcome_ready is False
    assert not any("1进2" in warning for warning in report.warnings)


def stored_report(version, *, pending=False):
    return ReviewAgentReportResponse(
        start_date=BASE, end_date=NEXT, sample_size=1, success_count=0, failed_count=0,
        pending_count=1, confidence=0.5, reviewed_picks=[pick()], generated_by=version,
        promotion_comparisons=[ReviewPromotionComparison(
            trade_date=BASE, next_trade_date=None if pending else NEXT,
            outcome_ready=not pending, top_pick_sample_size=1, top_pick_promoted_count=0,
            market_first_board_sample_size=1, market_promoted_count=0,
        )],
    )


@pytest.mark.parametrize("version,pending,recompute", [
    ("review-agent-tool-use-v5-position-label", False, True),
    ("review-agent-tool-use-v6-trading-calendar", False, True),
    ("review-agent-tool-use-v7-asof", False, True),
    (REVIEW_AGENT_VERSION, False, False),
    (REVIEW_AGENT_VERSION, True, True),
])
def test_route_only_reuses_current_ready_snapshot_and_preserves_original(tmp_path, version, pending, recompute):
    snapshots = SQLiteReviewSnapshotRepository(tmp_path / "review.sqlite")
    original = DailyReviewSnapshot(
        as_of_date=NEXT, start_date=BASE, report=stored_report(version, pending=pending),
        generated_by="daily-review-snapshot-v1", generated_at=datetime(2026, 10, 8, 8, tzinfo=timezone.utc),
    )
    snapshots.save_snapshot(original)
    before = snapshots.get_snapshot(NEXT).model_dump_json()
    event_repo = Mock()
    event_repo.list_events.return_value = [event(BASE, 1), event(NEXT, 2)]
    rebuilt = stored_report(REVIEW_AGENT_VERSION)
    with (
        patch("app.routers.agents.get_limit_up_repository", return_value=event_repo),
        patch("app.routers.agents.SQLiteFirstBoardRepository", return_value=repository()),
        patch("app.routers.agents.SQLiteReviewSnapshotRepository", return_value=snapshots),
        patch("app.routers.agents.build_review_agent_report", return_value=rebuilt) as build,
        patch.object(snapshots, "save_snapshot", wraps=snapshots.save_snapshot) as save,
    ):
        result = get_review_agent_report(
            start_date=None, end_date=NEXT, min_score=0, top_per_day=10, follow_days=5, use_llm=False,
        )
    assert result.generated_by == REVIEW_AGENT_VERSION
    save.assert_not_called()
    assert snapshots.get_snapshot(NEXT).model_dump_json() == before
    if recompute:
        build.assert_called_once()
        args = build.call_args.kwargs
        assert (args["start_date"], args["end_date"]) == (BASE, NEXT)
        assert (args["min_score"], args["top_per_day"], args["follow_days"]) == (0, 10, 5)
        assert isinstance(args["provider"], DisabledLLMProvider)
    else:
        build.assert_not_called()
