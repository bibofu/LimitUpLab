"""Synthetic set arithmetic must not inherit an implicit display-page cap."""

import pytest

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery


def population(store, start, end, **metadata):
    rows = [{"symbol": f"{number:06}", "rank": number} for number in range(start, end + 1)]
    return store.add(tool="synthetic_population", payload={"items": rows, "matched_count": len(rows), **metadata},
                     state="ok", arguments={})


@pytest.mark.parametrize("options", [{}, {"limit": None}])
def test_omitted_or_null_limit_preserves_full_evidence_and_only_paginates_preview(options):
    store = EvidenceStore()
    source = population(store, 1, 50)
    selected = store.compute(Compute(evidence_id=source, **options))
    record = store.get(selected)
    assert record["rows"] == store.get(source)["rows"]
    assert record["result_state"] == "ok" and not record["source_truncated"]
    assert record["payload"]["matched_count"] == record["payload"]["returned_count"] == 50
    assert record["arguments"]["limit"] is None
    preview = store.view(selected)
    assert preview["row_count"] == 50 and len(preview["rows"]) == 8
    assert preview["truncated"] and not preview["source_truncated"]


@pytest.mark.parametrize("operation,expected", [("intersection", 15), ("difference", 35)])
def test_full_set_comparison_count_and_complete_table_keep_all_members(operation, expected):
    store = EvidenceStore()
    source = population(store, 1, 50)
    other = population(store, 16, 30)
    selected = store.compute(Compute(evidence_id=source))
    result = store.compute(Compute(evidence_id=selected, other_id=other, operation=operation))
    counted = store.compute(Compute(evidence_id=result, operation="aggregate", aggregate="count"))
    assert len(store.get(result)["rows"]) == expected
    assert store.get(counted)["rows"][0]["value"] == expected
    assert store.get(result)["payload"]["matched_count"] == expected
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[result], table={
        "evidence_id": result, "columns": [{"field": "symbol", "label": "代码"}]})
    validate_delivery(final, TaskInterpretation(output_contract={"table_required": True}), store)
    assert len(render_answer(final, store).splitlines()) == expected + 2


@pytest.mark.parametrize("operation,expected", [("intersection", 5), ("difference", 15)])
def test_explicit_top_twenty_remains_a_usable_declared_comparison_scope(operation, expected):
    store = EvidenceStore()
    source = population(store, 1, 50)
    other = population(store, 16, 30)
    selected = store.compute(Compute(evidence_id=source, sort_by="rank", descending=False, limit=20))
    assert [row["rank"] for row in store.get(selected)["rows"]] == list(range(1, 21))
    assert store.get(selected)["payload"]["matched_count"] == 50
    assert store.get(selected)["payload"]["returned_count"] == 20
    assert store.get(selected)["arguments"]["limit"] == 20
    assert not store.get(selected)["source_truncated"]
    result = store.compute(Compute(evidence_id=selected, other_id=other, operation=operation))
    counted = store.compute(Compute(evidence_id=result, operation="aggregate"))
    assert len(store.get(result)["rows"]) == expected
    assert store.get(counted)["rows"][0]["value"] == expected
    assert store.get(result)["sources"] == [selected, other]


def test_union_of_two_full_populations_is_not_capped_at_explicit_limit_maximum():
    store = EvidenceStore()
    left, right = population(store, 1, 100), population(store, 101, 200)
    combined = store.compute(Compute(evidence_id=left, other_id=right, operation="union"))
    assert len(store.get(combined)["rows"]) == 200
    assert store.view(combined)["row_count"] == 200
    assert len(store.view(combined)["rows"]) == 8
    counted = store.compute(Compute(evidence_id=combined, operation="aggregate"))
    assert store.get(counted)["rows"][0]["value"] == 200


def test_unbounded_offset_retains_all_remaining_rows():
    store = EvidenceStore()
    source = population(store, 1, 50)
    selected = store.compute(Compute(evidence_id=source, sort_by="rank", descending=False, offset=3))
    assert len(store.get(selected)["rows"]) == 47
    assert store.get(selected)["rows"][0]["rank"] == 4
    assert [row["rank"] for row in store.get(selected)["rows"]] == list(range(4, 51))
    assert store.get(selected)["payload"]["returned_count"] == 47
    assert store.get(selected)["arguments"]["limit"] is None


@pytest.mark.parametrize("metadata", [
    {"source_truncated": True}, {"matched_count": 80}, {"data_missing": ["page unavailable"]},
])
def test_uncapped_computation_does_not_clear_source_gaps_or_enable_set_claims(metadata):
    store = EvidenceStore()
    source = population(store, 1, 50, **metadata)
    other = population(store, 16, 30)
    selected = store.compute(Compute(evidence_id=source))
    record = store.get(selected)
    assert len(record["rows"]) == 50 and record["result_state"] == "partial"
    assert record["source_truncated"] == store.get(source)["source_truncated"]
    assert record["data_missing"] == store.get(source)["data_missing"]
    for operation in ("difference", "intersection", "union"):
        with pytest.raises(ValueError, match="complete source sets"):
            store.compute(Compute(evidence_id=selected, other_id=other, operation=operation))
    counted = store.compute(Compute(evidence_id=selected, operation="aggregate"))
    assert store.get(counted)["result_state"] == "partial"


def test_uncapped_evidence_still_obeys_existing_render_budget():
    store = EvidenceStore()
    source = population(store, 1, 1001)
    selected = store.compute(Compute(evidence_id=source))
    assert len(store.get(selected)["rows"]) == 1001
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": selected, "columns": [{"field": "symbol", "label": "代码"}]})
    with pytest.raises(ValueError, match="exceeds 1000 rows"):
        render_answer(final, store)
