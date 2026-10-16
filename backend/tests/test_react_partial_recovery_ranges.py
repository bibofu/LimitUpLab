"""Recover requested windows only from replayable, complete current evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.partial_delivery import partial_delivery
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatRequest


FIRST_FIVE = [
    ("000011", "深物业A", 2, "09:37:00"),
    ("000090", "天健集团", 2, "10:19:00"),
    ("000503", "国新健康", 1, "09:33:00"),
    ("000560", "我爱我家", 1, "10:36:00"),
    ("000678", "襄阳轴承", 2, "10:41:00"),
]


def task(*, offset=0, limit=5):
    return TaskInterpretation(current_task="统计炸板总数，按股票代码升序展示所请求范围。",
        output_contract={"mode": "freeform", "table_required": True,
            "field_requests": [
                {"requested": "代码", "field": "symbol"},
                {"requested": "名称", "field": "name"},
                {"requested": "收盘涨跌幅", "field": "change_pct"},
                {"requested": "炸板次数", "field": "break_count"},
                {"requested": "首次触及涨停时间", "field": "first_limit_time"},
                {"requested": "炸板原因", "field": None},
            ],
            "row_selection": {"requested": "按股票代码升序的指定范围", "offset": offset, "limit": limit}})


def selected_store(*, count=49, offset=0, limit=5):
    rows = [{"symbol": symbol, "name": name, "break_count": breaks,
             "first_limit_time": first, "trade_date": "2026-10-16", "closed_limit": False}
            for symbol, name, breaks, first in FIRST_FIVE]
    rows += [{"symbol": f"600{i:03}", "name": f"合成{i}", "break_count": 1,
              "first_limit_time": "10:00:00", "trade_date": "2026-10-16", "closed_limit": False}
             for i in range(max(0, count - 5))]
    rows = list(reversed(rows[:count]))
    store = EvidenceStore()
    source = store.add(tool="limit_up_events", state="ok", arguments={"event_status": "failed"},
        payload={"events": rows, "matched_count": len(rows), "returned_count": len(rows),
                 "trade_date": "2026-10-16", "data_fresh": True})
    selected = store.compute(Compute(evidence_id=source, sort_by="symbol", descending=False,
                                    offset=offset, limit=limit))
    return store, source, selected


def test_q4_recovers_verified_total_and_five_rows_with_both_missing_columns():
    store, source, selected = selected_store()
    store.add(tool="market_event_pool", state="ok", arguments={}, payload={
        "items": [], "matched_count": 999, "result_mode": "count", "source": "无关计数"})
    interpretation = task()
    before = deepcopy(store.records)
    final = partial_delivery(store, interpretation, selected)
    assert final is not None and final.status == "partial"
    validate_delivery(final, interpretation, store)
    answer = render_answer(final, store, interpretation.output_contract)
    assert "本轮筛选范围内共49条，展示5条" in answer
    assert "999" not in answer and "无关计数" not in answer
    assert "| 代码 | 名称 | 收盘涨跌幅 | 炸板次数 | 首次触及涨停时间 | 炸板原因 |" in answer
    for symbol, name, breaks, first in FIRST_FIVE:
        assert f"| {symbol} | {name} | 数据未成功获取 | {breaks} | {first} | 数据未成功获取 |" in answer
    assert final.evidence_ids == [selected]
    assert final.table.evidence_id == selected
    assert store.records == before


def test_q4_recovery_passes_new_answer_through_independent_review(monkeypatch):
    store, _, selected = selected_store()
    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True)
    target = runtime.Run(AgentChatRequest(session_id="q4-range-recovery",
        message="炸板共多少只，按代码升序前五只及缺失原因。"), registry, object(), [], None)
    target.evidence = store
    target.task_context = task()
    target.answer = "被拒绝的旧正文不能重新发布。"
    reviews = []

    def review(*args, **kwargs):
        reviews.append(kwargs["answer"])
        return ComplianceReview(decision="allow", violations=[], reason="离线验证")

    monkeypatch.setattr(runtime, "review_answer", review)
    target.stop("validation_failed", table_evidence_id=selected)
    assert target.status == "partial" and target.reason == "validation_failed"
    assert len(reviews) == 1 and reviews[0] == target.answer
    assert "共49条，展示5条" in target.answer and "被拒绝的旧正文" not in target.answer
    assert "炸板原因" in target.answer and "数据未成功获取" in target.answer


@pytest.mark.parametrize("boundary", [
    "wrong_offset", "wrong_limit", "historical_parent", "stale_parent", "truncated_parent",
    "missing_parent_data", "missing_parent", "tampered_rows", "tampered_total",
    "unproved_source_total", "inconsistent_operation", "non_select", "earlier_slice",
    "raw_slice", "no_explicit_selection",
])
def test_window_recovery_rejects_unproved_or_changed_scope_without_mutating_store(boundary):
    store, source, selected = selected_store()
    interpretation = task()
    if boundary == "wrong_offset":
        interpretation = task(offset=1)
    elif boundary == "wrong_limit":
        interpretation = task(limit=4)
    elif boundary == "historical_parent":
        store.restore_history(source, store.get(source))
    elif boundary == "stale_parent":
        store.get(source)["payload"]["data_fresh"] = False
    elif boundary == "truncated_parent":
        store.get(source)["source_truncated"] = True
    elif boundary == "missing_parent_data":
        store.get(source)["data_missing"] = ["有来源未返回"]
    elif boundary == "missing_parent":
        del store.records[source]
    elif boundary == "tampered_rows":
        store.get(selected)["rows"][0]["name"] = "未出现在原证据的名字"
    elif boundary == "tampered_total":
        store.get(selected)["payload"]["matched_count"] = 999
    elif boundary == "unproved_source_total":
        store.get(source)["payload"].pop("matched_count")
    elif boundary == "inconsistent_operation":
        store.get(selected)["payload"]["operation"]["descending"] = True
    elif boundary == "non_select":
        selected = store.compute(Compute(evidence_id=source, operation="distinct",
                                        sort_by="symbol", descending=False, limit=5))
    elif boundary == "earlier_slice":
        prefix = store.compute(Compute(evidence_id=source, sort_by="symbol", descending=False, limit=10))
        selected = store.compute(Compute(evidence_id=prefix, sort_by="symbol", descending=False, limit=5))
    elif boundary == "raw_slice":
        selected = source
    else:
        selected = None
    before = deepcopy(store.records)
    assert partial_delivery(store, interpretation, selected) is None
    assert store.records == before


def test_complete_smaller_collection_can_satisfy_requested_maximum_without_fake_padding():
    store, _, selected = selected_store(count=3)
    interpretation = task()
    final = partial_delivery(store, interpretation, selected)
    assert final is not None
    assert "共3条，展示3条" in final.answer
    assert len(store.get(final.table.evidence_id)["rows"]) == 3


def test_window_beyond_complete_collection_is_not_recovered():
    store, _, selected = selected_store(offset=50)
    assert partial_delivery(store, task(offset=50), selected) is None


def test_replayed_filter_total_counts_filtered_scope_not_unfiltered_source():
    store, source, _ = selected_store()
    selected = store.compute(Compute(evidence_id=source, sort_by="symbol", descending=False, limit=5,
        filters=[{"field": "break_count", "operator": "eq", "value": 2}]))
    final = partial_delivery(store, task(), selected)
    assert final is not None
    assert "本轮筛选范围内共3条，展示3条" in final.answer
    assert "共49条" not in final.answer


def test_unknown_requested_column_cannot_gain_a_value_from_source_key_collision():
    store, source, _ = selected_store()
    interpretation = task()
    unknown = next(iter(interpretation.output_contract.unavailable_fields))
    for row in store.get(source)["rows"]:
        row[unknown] = "伪装为炸板原因的来源值"
    selected = store.compute(Compute(evidence_id=source, sort_by="symbol", descending=False, limit=5))
    final = partial_delivery(store, interpretation, selected)
    assert final is not None
    answer = render_answer(final, store, interpretation.output_contract)
    assert "伪装为炸板原因" not in answer
    assert "炸板原因" in answer and "数据未成功获取" in answer
