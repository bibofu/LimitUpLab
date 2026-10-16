"""Request meaning survives missing values, missing mappings and derived selections."""

from copy import deepcopy

import pytest

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery


def task_for(requests):
    return TaskInterpretation(output_contract={"table_required": True, "field_requests": [
        {"requested": label, "field": field} for field, label in requests]})


def candidate(key, task, status="partial"):
    return Finish(status=status, answer="{{evidence_table}}", evidence_ids=[key],
        missing=["部分请求字段的数据未成功获取"] if status == "partial" else [],
        table={"evidence_id": key, "columns": [
            {"field": field, "label": requested.requested[:40]}
            for field, requested in zip(task.output_contract.fields, task.output_contract.field_requests)]})


def test_unknown_column_retains_known_columns_and_cannot_read_provider_collision():
    task = task_for([("symbol", "代码"), (None, "炸板原因"), ("break_count", "炸板次数")])
    store = EvidenceStore()
    key = store.add(tool="limit_up_events", state="ok", arguments={}, payload={"events": [
        {"symbol": "000011", "break_count": 2, "unavailable_requested_2": 0},
    ]})
    before = deepcopy(store.records)
    final = candidate(key, task)
    validate_delivery(final, task, store)
    assert "| 000011 | 数据未成功获取 | 2 |" in render_answer(final, store, task.output_contract)
    assert store.records == before
    restored = TaskInterpretation.model_validate(task.model_dump())
    assert restored.output_contract == task.output_contract


@pytest.mark.parametrize("substitute", ["concept", "amount", None])
def test_unknown_column_cannot_be_substituted_or_omitted(substitute):
    task = task_for([("symbol", "代码"), (None, "炸板原因")])
    final = candidate("unused", task)
    if substitute is None:
        final.table.columns.pop()
    else:
        final.table.columns[-1].field = substitute
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(final, task)


def test_unknown_column_cannot_be_marked_complete_or_relabelled():
    task = task_for([("symbol", "代码"), (None, "炸板原因")])
    store = EvidenceStore()
    key = store.add(tool="limit_up_events", state="ok", arguments={}, payload={"events": [
        {"symbol": "000011", "unavailable_requested_2": 123},
    ]})
    with pytest.raises(ValueError, match="Unbound requested columns"):
        render_answer(candidate(key, task, "complete"), store, task.output_contract)
    final = candidate(key, task)
    final.table.columns[-1].label = "行业题材"
    with pytest.raises(ValueError, match="original requested labels"):
        validate_delivery(final, task, store)


def test_duplicate_unknown_labels_keep_distinct_stable_slots():
    task = task_for([("symbol", "代码"), (None, "未取得指标"), (None, "未取得指标")])
    assert task.output_contract.fields == ["symbol", "unavailable_requested_2", "unavailable_requested_3"]
    assert task.output_contract.unavailable_fields == {
        "unavailable_requested_2": "未取得指标", "unavailable_requested_3": "未取得指标"}


@pytest.mark.parametrize("field,wrong", [("seal_amount", "amount"), ("limit_up_reason", "concept")])
def test_limit_up_metrics_do_not_substitute_turnover_or_concept(field, wrong):
    task = task_for([("symbol", "代码"), (field, "封单金额" if field == "seal_amount" else "涨停原因")])
    store = EvidenceStore()
    key = store.add(tool="limit_up_events", state="ok", arguments={}, payload={"events": [
        {"symbol": "000011", "amount": 900000, "concept": "合成题材"},
    ]})
    final = candidate(key, task)
    validate_delivery(final, task, store)
    answer = render_answer(final, store, task.output_contract)
    assert "| 000011 | 数据未成功获取 |" in answer
    assert "900000" not in answer and "合成题材" not in answer
    final.table.columns[-1].field = wrong
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(final, task, store)


@pytest.mark.parametrize("computed", [False, True])
def test_previous_trade_day_gap_cannot_reuse_valid_source_zero(computed):
    task = task_for([("symbol", "代码"), ("rank", "排名"),
                     ("previous_trade_day_rank_change", "较上一交易日排名变化")])
    store = EvidenceStore()
    key = store.add(tool="hot_stock_ranking", state="ok", arguments={}, payload={"items": [
        {"symbol": "000011", "rank": 1, "rank_change": 0, "previous_trade_day_rank_change": None},
        {"symbol": "000012", "rank": 2, "rank_change": 1, "previous_trade_day_rank_change": 0},
    ]})
    if computed:
        key = store.compute(Compute(evidence_id=key, sort_by="rank", descending=False, limit=2))
    final = candidate(key, task)
    validate_delivery(final, task, store)
    answer = render_answer(final, store, task.output_contract)
    assert "| 000011 | 1 | 数据未成功获取 |" in answer
    assert "| 000012 | 2 | 0 |" in answer  # A confirmed zero remains zero.
    final.table.columns[-1].field = "rank_change"
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(final, task, store)
