"""Historical review facts must not borrow observations after their cutoff."""

from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from app.agents.review_agent import (
    ReviewAgentToolbox,
    _build_feature_comparison,
    _review_picks_from_toolbox,
    build_review_agent_report,
)
from app.models import AgentPrediction, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.evaluation_agent import _evaluate_prediction
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.review_as_of import build_review_post_bars, evaluate_review_prediction_as_of
from app.services.sample_data import SAMPLE_EVENTS


BASE = date(2026, 9, 28)
D1 = date(2026, 9, 29)
D2 = date(2026, 9, 30)
D3 = date(2026, 10, 8)
CALENDAR = (BASE, D1, D2, D3)
SYMBOL = "600001"
CREATED = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)


def prediction():
    return AgentPrediction(
        prediction_id="review-as-of", trade_date=BASE, symbol=SYMBOL,
        name="测试样本", score=90, rating="A", confidence=0.8,
        scoring_version="test", prediction_source="historical_backtest",
        data_as_of=BASE, facts_json={}, reasons=[], risks=[], created_at=CREATED,
    )


def event(day=BASE, *, height=1, continued=False):
    return SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "symbol": SYMBOL, "board_height": height,
        "closed_limit": True, "continued_next_day": continued,
    })


def bars():
    prices = ((10, 11, 9, 10), (11, 13, 10, 12), (12, 14, 11, 13), (13, 15, 8, 9))
    return [
        StockDailyBar(
            symbol=SYMBOL, trade_date=day, open=opened, high=high, low=low,
            close=close, volume=1_000_000, amount=10_000_000,
            source="test", created_at=CREATED,
        )
        for day, (opened, high, low, close) in zip(CALENDAR, prices)
    ]


def evaluate(cutoff, *, observed_bars=None, base_event=None, calendar=CALENDAR):
    return evaluate_review_prediction_as_of(
        prediction(), event=base_event or event(continued=True),
        bars=bars() if observed_bars is None else observed_bars,
        events=[event(), event(D1, height=2), event(D3, height=2)],
        trade_dates=calendar, as_of_date=cutoff,
    )


def test_base_day_cutoff_hides_cached_future_outcomes_and_continuation_flag():
    original_event = event(continued=True)
    original_prediction = prediction()
    original_bars = bars()
    before = (original_event.model_dump(), original_prediction.model_dump(),
              [bar.model_dump() for bar in original_bars])
    result = evaluate_review_prediction_as_of(
        original_prediction, event=original_event, bars=original_bars,
        events=[event(D1, height=2)], trade_dates=CALENDAR, as_of_date=BASE,
    )

    assert result.evaluation_label == "pending"
    assert result.outcome_ready is False
    assert result.promoted_to_second_board is False
    assert result.next_open_to_close_pct is None
    assert result.next_high_pct is None
    assert result.three_day_close_pct is None
    assert result.max_drawdown_from_next_open_3d is None
    assert (original_event.model_dump(), original_prediction.model_dump(),
            [bar.model_dump() for bar in original_bars]) == before


def test_next_day_cutoff_keeps_next_day_but_not_three_day_facts():
    result = evaluate(D1)

    assert result.evaluation_label == "success"
    assert result.outcome_ready is True
    assert result.promoted_to_second_board is True
    assert result.next_open_to_close_pct == pytest.approx(9.09)
    assert result.three_day_high_pct is None
    assert result.three_day_close_pct is None
    assert result.three_day_open_to_close_pct is None
    assert result.max_drawdown_from_next_open_3d is None


def test_three_day_facts_become_available_only_when_the_window_has_closed():
    result = evaluate(D3)

    assert result.next_open_to_close_pct == pytest.approx(9.09)
    assert result.three_day_close_pct == pytest.approx(-10)
    assert result.three_day_open_to_close_pct == pytest.approx(-18.18)
    assert result.max_drawdown_from_next_open_3d == pytest.approx(-27.27)


def test_missing_next_trading_day_is_not_replaced_by_a_later_bar():
    result = evaluate(D3, observed_bars=[bar for bar in bars() if bar.trade_date != D1])

    assert result.evaluation_label == "pending"
    assert result.outcome_ready is False
    assert result.next_open_to_close_pct is None
    assert result.three_day_close_pct is None


@pytest.mark.parametrize("calendar", [(), (D1, D2, D3)])
def test_unknown_or_missing_base_calendar_does_not_infer_days_from_cached_bars(calendar):
    result = evaluate(D3, calendar=calendar)

    assert result.evaluation_label == "pending"
    assert result.outcome_ready is False
    assert result.promoted_to_second_board is False
    assert result.next_close_pct is None
    assert result.three_day_close_pct is None


@pytest.mark.parametrize("missing", ["event", "base_bar"])
def test_missing_base_observation_keeps_the_evaluation_pending(missing):
    result = evaluate_review_prediction_as_of(
        prediction(), event=None if missing == "event" else event(),
        bars=bars()[1:] if missing == "base_bar" else bars(),
        events=[event(D1, height=2)], trade_dates=CALENDAR, as_of_date=D3,
    )

    assert result.evaluation_label == "pending"
    assert result.outcome_ready is False
    assert result.next_close_pct is None
    assert result.three_day_close_pct is None


def test_stale_continuation_flag_cannot_prove_promotion_without_next_day_event():
    result = evaluate_review_prediction_as_of(
        prediction(), event=event(continued=True), bars=bars(),
        events=[event(), event(D3, height=2)], trade_dates=CALENDAR, as_of_date=D1,
    )

    assert result.outcome_ready is True
    assert result.promoted_to_second_board is False


def test_display_bars_stop_at_the_same_cutoff_and_keep_the_original_anchor():
    result = build_review_post_bars(
        list(reversed(bars())), symbol=SYMBOL, base_date=BASE, as_of_date=D1, follow_days=5,
        trade_dates=CALENDAR,
    )

    assert [bar.trade_date for bar in result] == [BASE, D1]
    assert [bar.return_from_base_pct for bar in result] == pytest.approx([0, 20])


def test_missing_base_bar_never_turns_a_later_close_into_the_return_anchor():
    result = build_review_post_bars(
        bars()[1:], symbol=SYMBOL, base_date=BASE, as_of_date=D3, follow_days=5,
        trade_dates=CALENDAR,
    )

    assert [bar.trade_date for bar in result] == [D1, D2, D3]
    assert all(bar.return_from_base_pct is None for bar in result)


class CapturingProvider(LLMProvider):
    def __init__(self):
        self.report_input = None

    def generate(self, system_prompt, user_prompt):
        if "planner" in system_prompt.lower():
            content = '{"tool_calls":[{"name":"pick_outcomes"}]}'
        else:
            self.report_input = json.loads(user_prompt)
            content = json.dumps({
                "main_findings": ["截至当日的后续行情尚未就绪，暂时不能评价兑现。"],
                "adjustment_suggestions": ["补齐截至日内的必要行情后再作描述性比较。"],
                "confidence": 0.5,
            }, ensure_ascii=False)
        return LLMResult(content=content, provider="test", model="as-of-capture")

    def generate_messages(self, messages, tools, **kwargs):
        return AIMessage(content="", tool_calls=[{
            "id": "review-check", "name": "submit_compliance_review",
            "args": {"decision": "allow", "violations": [], "reason": "仅描述数据限制"},
        }])


def test_model_tool_facts_and_report_use_cutoff_results_instead_of_saved_future_labels(monkeypatch):
    saved_prediction = prediction()
    future_evaluation = evaluate(D3)
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_predictions_between.return_value = [saved_prediction]
    repo.list_post_bars.return_value = bars()
    monkeypatch.setattr("app.agents.review_agent.build_agent_evaluation", lambda **kwargs: SimpleNamespace(
        prediction_count=1, evaluations=[future_evaluation],
    ))
    monkeypatch.setattr("app.agents.review_agent.build_top10_outcome_completeness",
                        lambda **kwargs: SimpleNamespace(warnings=[]))
    provider = CapturingProvider()
    report = build_review_agent_report(
        events=[event(continued=True), event(D1, height=2), event(D3, height=2)],
        start_date=BASE, end_date=BASE, repository=repo, provider=provider,
        trade_dates=CALENDAR,
    )

    assert report.generation_mode == "llm"
    assert (report.success_count, report.failed_count, report.pending_count) == (0, 0, 1)
    reviewed, = report.reviewed_picks
    assert [bar.trade_date for bar in reviewed.post_bars] == [BASE]
    assert reviewed.next_open_to_close_pct is None
    assert reviewed.three_day_close_pct is None
    assert reviewed.promoted_to_second_board is False
    outcome_facts = provider.report_input["tool_facts"]["pick_outcomes"]
    assert outcome_facts["success_count"] == 0
    assert outcome_facts["pending_count"] == 1
    assert outcome_facts["outcomes"][0]["three_day_close_pct"] is None
    assert outcome_facts["outcomes"][0]["promoted_to_second_board"] is None
    assert outcome_facts["outcomes"][0]["promotion_outcome_ready"] is False
    authority = provider.report_input["tool_facts"]["authoritative_review"]
    assert authority["evaluation_label_counts"] == {"pending": 1}
    assert authority["sampling_limits"]["as_of_date"] == BASE.isoformat()
    assert "500" not in authority["sampling_limits"]["note"]
    assert "截至日" in authority["comparison_basis"]["feature_groups"]
    assert future_evaluation.evaluation_label == "success"
    assert future_evaluation.three_day_close_pct == -10
    repo.upsert_outcomes.assert_not_called()
    repo.upsert_predictions.assert_not_called()


def test_daily_selection_does_not_drop_a_top_score_after_future_label_ordered_500_items(monkeypatch):
    predictions = [prediction().model_copy(update={
        "prediction_id": f"candidate-{index}", "symbol": f"600{index:03d}",
        "score": 99 if index == 500 else 70,
    }) for index in range(501)]
    evaluations = [_evaluate_prediction(item, None) for item in predictions]
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_predictions_between.return_value = predictions
    repo.list_post_bars.side_effect = lambda symbol, base, **kwargs: [
        bars()[0].model_copy(update={"symbol": symbol})
    ]
    received_limits = []

    def evaluate_all(**kwargs):
        received_limits.append(kwargs["limit"])
        return SimpleNamespace(prediction_count=501, evaluations=evaluations[:kwargs["limit"]])

    monkeypatch.setattr("app.agents.review_agent.build_agent_evaluation", evaluate_all)
    toolbox = ReviewAgentToolbox(
        events=[event().model_copy(update={"symbol": item.symbol}) for item in predictions],
        repository=repo, start_date=BASE, end_date=BASE, min_score=0,
        top_per_day=1, follow_days=5, trade_dates=CALENDAR,
    )

    selected, = toolbox._high_score_evaluations()
    assert received_limits == [501]
    assert selected.prediction_id == "candidate-500"
    assert selected.score == 99
    assert selected.evaluation_label == "pending"
    assert toolbox.evaluation_scope["candidates_truncated"] is False


def test_missing_d1_cannot_extend_a_one_day_window_to_d2():
    result = build_review_post_bars(
        [bar for bar in bars() if bar.trade_date != D1], symbol=SYMBOL,
        base_date=BASE, as_of_date=D3, follow_days=1, trade_dates=CALENDAR,
    )
    assert [bar.trade_date for bar in result] == [BASE]
    assert [bar.trading_day_offset for bar in result] == [0]


def test_known_later_observation_keeps_its_calendar_slot_across_a_gap():
    result = build_review_post_bars(
        [bar for bar in bars() if bar.trade_date != D1], symbol=SYMBOL,
        base_date=BASE, as_of_date=D3, follow_days=2, trade_dates=CALENDAR,
    )
    assert [bar.trade_date for bar in result] == [BASE, D2]
    assert [bar.trading_day_offset for bar in result] == [0, 2]
    assert result[-1].return_from_base_pct == pytest.approx(30)


def review_toolbox(*, observed_bars=None, observed_events=None, calendar=CALENDAR, follow_days=5):
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_predictions_between.return_value = [prediction()]
    repo.list_post_bars.return_value = bars() if observed_bars is None else observed_bars
    toolbox = ReviewAgentToolbox(
        events=[event()] if observed_events is None else observed_events,
        repository=repo, start_date=BASE, end_date=D3, min_score=0,
        top_per_day=10, follow_days=follow_days, trade_dates=calendar,
    )
    toolbox._evaluations = [evaluate(D3, observed_bars=observed_bars, calendar=calendar)]
    return toolbox


def test_missing_market_event_day_does_not_shrink_expected_bar_calendar():
    toolbox = review_toolbox(
        observed_bars=[bar for bar in bars() if bar.trade_date in {BASE, D2}],
        observed_events=[event(), event(D2)], follow_days=2,
    )
    pick, = _review_picks_from_toolbox(toolbox)
    assert pick.expected_post_bar_count == 3
    assert pick.post_bar_cache_complete is False
    assert [bar.trading_day_offset for bar in pick.post_bars] == [0, 2]


def test_unknown_calendar_never_claims_complete_cache_or_assigns_followup_slots():
    pick, = _review_picks_from_toolbox(review_toolbox(calendar=()))
    assert pick.expected_post_bar_count == 0
    assert pick.post_bar_cache_complete is False
    assert [bar.trade_date for bar in pick.post_bars] == [BASE]
    assert pick.post_bars[0].trading_day_offset == 0


@pytest.mark.parametrize("missing", ["next_bar", "next_events"])
def test_unknown_promotion_never_enters_feature_denominator_or_false_tool_fact(missing):
    observed = [bar for bar in bars() if missing != "next_bar" or bar.trade_date != D1]
    toolbox = review_toolbox(observed_bars=observed, observed_events=[event()])
    comparison = toolbox.feature_comparison()
    assert comparison["failed_count"] == 1
    assert comparison["metrics"]["sample_counts"]["failed"]["promotion"] == {
        "valid_count": 0, "missing_count": 1,
    }
    facts = toolbox.pick_outcomes().output
    assert facts["promotion_unknown_count"] == 1
    assert facts["outcomes"][0]["promotion_outcome_ready"] is False
    assert facts["outcomes"][0]["promoted_to_second_board"] is None


def test_independently_known_promotion_is_visible_but_pending_ohlc_stays_out_of_feature_rate():
    toolbox = review_toolbox(
        observed_bars=[bar for bar in bars() if bar.trade_date != D1],
        observed_events=[event(), event(D1, height=2)],
    )
    facts = toolbox.pick_outcomes().output["outcomes"][0]
    assert facts["outcome_ready"] is False
    assert facts["promotion_outcome_ready"] is True
    assert facts["promoted_to_second_board"] is True
    counts = toolbox.feature_comparison()["metrics"]["sample_counts"]["failed"]["promotion"]
    assert counts == {"valid_count": 0, "missing_count": 1}


def test_metric_denominators_exclude_missing_values_and_are_disclosed_in_text():
    predictions = {}
    evaluations = []
    group_labels = {}
    tracked_returns = {}
    promotions = {}
    for index in range(6):
        key = f"metric-{index}"
        predictions[key] = prediction().model_copy(update={"prediction_id": key})
        evaluations.append(evaluate(D3).model_copy(update={
            "prediction_id": key,
            "max_drawdown_from_next_open_3d": None if index % 3 == 2 else -10,
        }))
        group_labels[key] = "success" if index < 3 else "miss"
        tracked_returns[key] = 10 if index < 3 else -10
        if index % 3 != 2:
            promotions[key] = index % 3 == 0
    comparison = _build_feature_comparison(
        evaluations, predictions, group_labels=group_labels,
        tracked_returns=tracked_returns, promotion_results=promotions,
    )
    for group in ("success", "failed"):
        metrics = comparison["metrics"]
        assert metrics[group]["promotion"] == 0.5
        assert metrics["sample_counts"][group]["promotion"] == {"valid_count": 2, "missing_count": 1}
        assert metrics["sample_counts"][group]["max_drawdown_from_next_open_3d"] == {"valid_count": 2, "missing_count": 1}
        assert metrics["sample_counts"][group]["return_20d_pct"] == {"valid_count": 0, "missing_count": 3}
    patterns = " ".join(comparison["successful_patterns"])
    assert "晋级率 50.0%（有效2/3只）" in patterns
    assert "三日最大回撤平均 -10.00%（有效2/3只）" in patterns
