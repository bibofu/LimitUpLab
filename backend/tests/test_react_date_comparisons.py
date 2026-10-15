"""Trading-day comparisons are semantic requests resolved by a real calendar."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime as runtime_module
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.date_scope import DateScope
from app.agents.react_runtime.runtime import Run, run
from app.agents.react_runtime.tools import ToolGateway
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.prompt_security import review_input


def comparison(anchor="2026-10-15", offset=-1):
    return DateScope(mode="dates", dates=[anchor], trading_day_comparisons=[
        {"anchor_date": anchor, "offset": offset}])


@pytest.mark.parametrize("anchor,offset,sessions,expected", [
    ("2026-10-15", -1, ["2026-10-14", "2026-10-15"], "2026-10-14"),
    ("2026-10-12", -1, ["2026-10-09", "2026-10-12"], "2026-10-09"),
    ("2026-10-09", -1, ["2026-09-30", "2026-10-09"], "2026-09-30"),
    ("2026-10-09", 1, ["2026-10-09", "2026-10-12"], "2026-10-12"),
    ("2026-10-13", -2, ["2026-10-09", "2026-10-12", "2026-10-13"], "2026-10-09"),
    ("2026-10-11", -1, ["2026-10-09", "2026-10-12"], "2026-10-09"),
])
def test_comparison_uses_exchange_sessions_including_weekends_and_holidays(anchor, offset, sessions, expected):
    scope, missing = comparison(anchor, offset).resolve_comparisons(
        lambda start, end: [date.fromisoformat(day) for day in sessions])
    assert scope.dates == [date.fromisoformat(anchor), date.fromisoformat(expected)]
    assert missing == []
    assert scope.single_date is None


@pytest.mark.parametrize("sessions", [[], [date(2026, 9, 30)], [date(2026, 10, 15)], ["2026-10-14"]])
def test_incomplete_calendar_does_not_guess_or_authorize_a_comparison_day(sessions):
    scope, missing = comparison().resolve_comparisons(lambda *_: sessions)
    assert scope.dates == [date(2026, 10, 15)]
    assert len(missing) == 1 and "交易日历" in missing[0]
    with pytest.raises(ValueError, match="conflicts"):
        scope.bind(("trade_date",), {"trade_date": "2026-10-14"})


def test_calendar_failure_is_a_data_gap_without_leaking_provider_errors():
    def unavailable(*_):
        raise RuntimeError("private provider details")
    scope, missing = comparison().resolve_comparisons(unavailable)
    assert scope.single_date == date(2026, 10, 15)
    assert missing and "private" not in missing[0]


@pytest.mark.parametrize("scope", [
    {"mode": "dates", "dates": ["2026-10-15"]},
    {"mode": "dates", "dates": ["2026-10-14", "2026-10-15"]},
    {"mode": "range", "start_date": "2026-10-09", "end_date": "2026-10-15"},
    {"mode": "dates", "basis": "event", "dates": ["2026-10-14"], "observation_cutoff": "2026-10-15"},
])
def test_explicit_dates_ranges_and_event_cutoffs_do_not_fetch_or_expand_calendar(scope):
    original = DateScope.model_validate(scope)
    def unexpected(*_):
        pytest.fail("An explicit date task does not need calendar resolution")
    resolved, missing = original.resolve_comparisons(unexpected)
    assert resolved == original and missing == []


@pytest.mark.parametrize("update", [
    {"mode": "range", "dates": [], "start_date": "2026-10-09", "end_date": "2026-10-15"},
    {"basis": "event"},
    {"dates": ["2026-10-14"]},
    {"trading_day_comparisons": [{"anchor_date": "2026-10-15", "offset": 0}]},
    {"trading_day_comparisons": [{"anchor_date": "2026-10-15", "offset": True}]},
])
def test_comparison_cannot_bypass_date_basis_or_anchor_requirements(update):
    with pytest.raises(ValueError):
        DateScope.model_validate({**comparison().model_dump(mode="json"), **update})


class ReviewProvider:
    def __init__(self, scope):
        self.scope = scope

    def generate_messages(self, messages, tools, **_):
        schema = tools[0]["function"]["parameters"]
        assert "trading_day_comparisons" in schema["$defs"]["_ReviewDateScope"]["required"]
        return AIMessage(content="", tool_calls=[{"id": "review", "name": "submit_input_security_review", "args": {
            "decision": "allow", "signals": [], "reason": "semantic fixture", "request_kind": "research",
            "context_mode": "follow_up", "current_task": "查询10月15日涨停数并与上一交易日比较。",
            "date_scope": self.scope.model_dump(mode="json"),
        }}])


def test_review_resolves_current_explicit_anchor_instead_of_inheriting_previous_task_date(monkeypatch):
    monkeypatch.setattr("app.collectors.trading_calendar_collector.collect_a_share_trade_dates",
                        lambda *_: [date(2026, 10, 14), date(2026, 10, 15)])
    result = review_input(ReviewProvider(comparison()), message="那10月15日呢，与上一交易日比多少？",
                         timeout_seconds=5, anchor_date=date(2026, 10, 15),
                         task_context={"current_task": "查询9月30日的涨停数量"})
    assert result.date_scope.dates == [date(2026, 10, 15), date(2026, 10, 14)]
    assert result.date_resolution_missing == []
    assert result.requested_date is None and result.time_scope == "explicit"


def test_review_calendar_gap_is_not_misclassified_as_missing_user_input(monkeypatch):
    monkeypatch.setattr("app.collectors.trading_calendar_collector.collect_a_share_trade_dates", lambda *_: [])
    result = review_input(ReviewProvider(comparison()), message="比较上一交易日", timeout_seconds=5)
    assert result.date_resolution_missing
    assert result.pending_slots == []


@pytest.mark.parametrize("message", ["不要比较，只查10月15日。", "不要14日，改查15日。", "过去5个交易日的行情截至15日。"])
def test_review_does_not_reintroduce_negated_corrected_or_lookback_dates(message, monkeypatch):
    def unexpected(*_):
        pytest.fail("No semantic comparison was requested")
    monkeypatch.setattr("app.collectors.trading_calendar_collector.collect_a_share_trade_dates", unexpected)
    result = review_input(ReviewProvider(DateScope(mode="dates", dates=[date(2026, 10, 15)])),
                         message=message, timeout_seconds=5)
    assert result.date_scope.dates == [date(2026, 10, 15)]


def test_comparison_contract_admits_both_dates_but_no_unrequested_third_day():
    scope, _ = comparison().resolve_comparisons(lambda *_: [date(2026, 10, 14), date(2026, 10, 15)])
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    gateway = ToolGateway(registry, EvidenceStore())
    gateway.date_scope = scope
    for day in ("2026-10-14", "2026-10-15"):
        assert gateway.validate({"name": "market_event_pool", "args": {"event_type": "limit_up", "trade_date": day}})["trade_date"] == day
    with pytest.raises(ValueError, match="comparison dates"):
        gateway.validate({"name": "market_event_pool", "args": {"event_type": "limit_up", "trade_date": "2026-10-13"}})


def test_graph_completes_same_session_follow_up_with_both_count_queries(monkeypatch):
    monkeypatch.setattr("app.collectors.trading_calendar_collector.collect_a_share_trade_dates",
                        lambda *_: [date(2026, 10, 14), date(2026, 10, 15)])
    monkeypatch.setattr(runtime_module, "review_answer", lambda *_args, **_kwargs:
                        ComplianceReview(decision="allow", violations=[], reason="fixture"))
    queried = []
    def count(**args):
        queried.append(args["trade_date"])
        return ToolResult(name="market_event_pool", input=args, summary="dated count fixture", output={
            "items": [], "trade_date": args["trade_date"], "matched_count": 36 if args["trade_date"] == "2026-10-15" else 30})
    class Provider(ReviewProvider):
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_input_security_review":
                return super().generate_messages(messages, tools, **kwargs)
            observations = [json.loads(item.content) for item in messages if isinstance(item, ToolMessage)]
            if not observations:
                return AIMessage(content="", tool_calls=[{
                    "id": day, "name": "market_event_pool", "args": {
                        "event_type": "limit_up", "trade_date": day, "result_mode": "count"}}
                    for day in ("2026-10-15", "2026-10-14")])
            return AIMessage(content="", tool_calls=[{"id": "final", "name": "finish", "args": {
                "status": "complete", "answer": "10月15日涨停36只，10月14日30只，增加6只。",
                "evidence_ids": [item["evidence_id"] for item in observations], "missing": []}}])
    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, market_event_pool=count)
    result = run(AgentChatRequest(session_id="comparison", message="那10月15日呢？与上一交易日相比增减多少？"),
                 registry, Provider(comparison()))
    assert result.task_status == "complete"
    assert sorted(queried) == ["2026-10-14", "2026-10-15"]
    assert "增加6只" in result.answer
    assert not [trace for trace in result.tool_results if trace.name == "react_policy" and trace.status == "error"]


def test_calendar_gap_survives_checkpoint_and_cannot_finish_as_complete():
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    request = AgentChatRequest(session_id="calendar-gap", message="比较涨停数量")
    original = Run(request, registry, object(), [], None)
    original.date_resolution_missing = ["交易日历暂不可用，无法确认上一交易日。"]
    snapshots = []
    original.control = SimpleNamespace(save=snapshots.append)
    original.save({"messages": []}, "agent")
    restored = Run(request, registry, object(), [], None)
    restored.control = SimpleNamespace(row={"checkpoint_json": json.dumps(snapshots[0])}, cancelled=lambda: False)
    restored.restore()
    assert restored.date_resolution_missing == original.date_resolution_missing
    result = restored.gate({"messages": [], "finish": {"status": "complete", "answer": "已经比较完毕。"}})
    assert result["finish"] is None
    assert "Comparison dates could not be verified" in restored.traces[-1].output["reason"]
