"""Missing data must not discard verified results or relax evidence boundaries."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatRequest


MISSING = "数据未成功获取"
LABELS = {
    "symbol": "代码", "name": "名称", "amount": "成交额", "source": "数据来源",
    "closed_limit": "收盘封板", "close": "收盘点位", "end_close": "收盘点位",
    "trade_date": "数据日期", "end_date": "数据日期", "change_pct": "涨跌幅",
    "concept": "数据来源", "unavailable_custom": "未知自定义指标",
}


def interpretation(fields, **updates):
    return TaskInterpretation(output_contract={
        "mode": "freeform", "table_required": True, "fields": fields, **updates,
    })


def finish(key, fields, *, status="partial", missing=None):
    return Finish(
        status=status, answer="已取得以下数据。\n\n{{evidence_table}}\n\n未取得的字段已注明。",
        missing=(["请求字段未全部取得"] if status == "partial" else []) if missing is None else missing,
        evidence_ids=[key], table={"evidence_id": key, "columns": [
            {"field": field, "label": LABELS[field]} for field in fields]},
    )


def event_store(rows):
    store = EvidenceStore()
    key = store.add(tool="limit_up_events", state="ok", arguments={}, payload={"events": rows})
    return store, key


def test_partial_table_retains_available_zero_and_false_and_marks_missing_column():
    fields = ["symbol", "name", "amount", "closed_limit", "source"]
    task = interpretation(fields)
    store, key = event_store([
        {"symbol": "600101", "name": "合成甲", "amount": 0, "closed_limit": False},
        {"symbol": "600102", "name": "合成乙", "amount": 120, "closed_limit": True,
         "source": "合成事件来源"},
    ])
    before = deepcopy(store.records)
    final = finish(key, fields)
    validate_delivery(final, task, store)
    answer = render_answer(final, store, output_contract=task.output_contract)
    assert f"| 600101 | 合成甲 | 0 | False | {MISSING} |" in answer
    assert "| 600102 | 合成乙 | 120 | True | 合成事件来源 |" in answer
    assert store.records == before


@pytest.mark.parametrize("status,missing", [("complete", []), ("partial", [])])
def test_missing_table_column_requires_explicit_partial_gap(status, missing):
    fields = ["symbol", "source"]
    task = interpretation(fields)
    store, key = event_store([{"symbol": "600101"}])
    with pytest.raises(ValueError):
        render_answer(finish(key, fields, status=status, missing=missing), store,
                      output_contract=task.output_contract)


def test_partial_table_does_not_turn_unknown_missing_field_into_a_valid_column():
    store, key = event_store([{"symbol": "600101"}])
    with pytest.raises(ValueError):
        render_answer(finish(key, ["symbol", "unavailable_custom"]), store,
                      output_contract=interpretation(["symbol"]).output_contract)


@pytest.mark.parametrize("bad_value", [{"source": "嵌套内容"}, ["合成来源"], float("nan")])
def test_partial_missing_permission_does_not_accept_non_scalar_or_non_finite_cells(bad_value):
    fields = ["symbol", "source"]
    store, key = event_store([{"symbol": "600101", "source": bad_value}])
    with pytest.raises(ValueError):
        render_answer(finish(key, fields), store,
                      output_contract=interpretation(fields).output_contract)


@pytest.mark.parametrize("declared", [
    ["name", "close", "change_pct", "amount", "trade_date", "source"],
    ["name", "end_close", "change_pct", "amount", "end_date", "source"],
])
def test_index_aliases_preserve_actual_close_and_date_when_amount_is_missing(declared):
    task = interpretation(["name", "close", "change_pct", "amount", "trade_date", "source"])
    store = EvidenceStore()
    key = store.add(tool="market_index_trend", state="ok", arguments={}, payload={"indices": [
        {"name": "合成指数", "end_close": 3716.28, "change_pct": 0.24,
         "end_date": "2026-10-16", "source": "合成指数来源"},
    ]})
    final = finish(key, declared)
    validate_delivery(final, task, store)
    answer = render_answer(final, store, output_contract=task.output_contract)
    assert f"| 合成指数 | 3716.28 | 0.24 | {MISSING} | 2026-10-16 | 合成指数来源 |" in answer


def test_index_aliases_do_not_apply_to_other_tools_with_similar_row_keys():
    store = EvidenceStore()
    key = store.add(tool="stock_kline", state="ok", arguments={}, payload={"bars": [
        {"end_close": 10.1, "end_date": "2026-10-16"},
    ]})
    with pytest.raises(ValueError):
        validate_delivery(finish(key, ["end_close", "end_date"]),
                          interpretation(["close", "trade_date"]), store)


def test_concept_cannot_impersonate_missing_source_even_with_source_label():
    store, key = event_store([{"symbol": "600101", "concept": "人工智能"}])
    with pytest.raises(ValueError):
        validate_delivery(finish(key, ["symbol", "concept"]),
                          interpretation(["symbol", "source"]), store)


def recovery_run():
    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True)
    target = runtime.Run(AgentChatRequest(session_id="partial-data-delivery",
        message="列出股票代码、名称和数据来源，未取得的数据请注明。"), registry, object(), [], None)
    target.task_context = interpretation(["symbol", "name", "source"])
    key = target.evidence.add(tool="limit_up_events", state="ok", arguments={}, payload={
        "events": [{"symbol": "600101", "name": "已核验对象"}], "data_fresh": True,
    })
    return target, key


def test_validation_recovery_selects_current_dataset_and_reviews_new_answer(monkeypatch):
    target, key = recovery_run()
    target.evidence.add(tool="hot_stock_ranking", state="ok", arguments={}, payload={"items": [
        {"symbol": "600202", "name": "无关榜单对象", "source": "无关来源"},
    ]})
    target.answer = "旧候选回答包含未经核验的结论。"
    reviewed = []

    def review(*args, **kwargs):
        reviewed.append(kwargs["answer"])
        return ComplianceReview(decision="allow", violations=[], reason="合成离线审查")

    monkeypatch.setattr(runtime, "review_answer", review)
    before = deepcopy(target.evidence.records)
    target.stop("validation_failed", table_evidence_id=key)
    assert target.status == "partial"
    assert target.reason == "validation_failed"
    assert len(reviewed) == 1 and reviewed[0] == target.answer
    assert "600101" in target.answer and "已核验对象" in target.answer and MISSING in target.answer
    assert "600202" not in target.answer and "无关榜单对象" not in target.answer
    assert "旧候选" not in target.answer and "未经核验的结论" not in target.answer
    assert target.evidence.records == before
    passed = [t.output for t in target.traces if t.name == "react_fallback_delivery" and t.output.get("passed")]
    assert len(passed) == 1 and passed[0]["finish"]["table"]["evidence_id"] == key


@pytest.mark.parametrize("boundary", ["cancelled", "historical", "stale", "rank_window", "deadline"])
def test_selected_recovery_does_not_relax_scope_freshness_cancellation_or_rank_proof(monkeypatch, boundary):
    target, key = recovery_run()
    if boundary == "cancelled":
        target.control = SimpleNamespace(cancelled=lambda: True)
    elif boundary == "historical":
        target.evidence.restore_history(key, target.evidence.get(key))
    elif boundary == "stale":
        target.evidence.get(key)["payload"]["data_fresh"] = False
    elif boundary == "rank_window":
        target.task_context = interpretation(["symbol", "name", "source"], row_selection={
            "requested": "第2至3名", "offset": 1, "limit": 2})
    else:
        target.deadline = 0

    def unexpected_review(*args, **kwargs):
        pytest.fail("Ineligible recovery must not invoke an independent model review")

    monkeypatch.setattr(runtime, "review_answer", unexpected_review)
    before = deepcopy(target.evidence.records)
    target.stop("validation_failed", table_evidence_id=key)
    assert "| 600101 |" not in target.answer
    assert not any(t.output.get("passed") for t in target.traces if t.name == "react_fallback_delivery")
    assert target.evidence.records == before
    if boundary == "cancelled":
        assert target.status == "cancelled"


@pytest.mark.parametrize("review_failure", ["reject", "exception"])
def test_recovery_never_publishes_cells_rejected_by_independent_review(monkeypatch, review_failure):
    target, key = recovery_run()
    target.evidence.get(key)["rows"][0]["name"] = "不得未经审查再次回显的单元格"

    def review(*args, **kwargs):
        if review_failure == "exception":
            raise RuntimeError("合成审查服务异常")
        return ComplianceReview(decision="reject", violations=["trade_instruction"], reason="合成拒绝")

    monkeypatch.setattr(runtime, "review_answer", review)
    target.stop("validation_failed", table_evidence_id=key)
    assert "不得未经审查再次回显的单元格" not in target.answer
    assert "| 600101 |" not in target.answer
    assert target.compliance_checks == 1
