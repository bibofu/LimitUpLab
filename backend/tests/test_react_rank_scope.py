"""Synthetic protocol tests only; these are not real-market evaluation cases."""

from copy import deepcopy
from datetime import date

import pytest

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore, payload_of
from app.agents.react_runtime.rendering import render_answer
from app.agents.tools import AgentToolRegistry
from app.services.sample_data import SAMPLE_EVENTS


def source_fixture(limit=100, direction="desc"):
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events = [SAMPLE_EVENTS[0].model_copy(update={
        "symbol": f"600{i:03d}", "name": f"protocol-only-{i}",
        "trade_date": date(2026, 9, 21), "closed_limit": True,
        "amount": float(1000 - i // 2), "board_height": 1 if i % 2 else 2,
    }) for i in range(103)]
    result = registry.limit_up_events(trade_date=date(2026, 9, 21),
        sort_by="amount", sort_order=direction, limit=limit)
    return payload_of(result), result.input


def final(key):
    return Finish(status="complete", answer="排名范围内的结果：\n{{evidence_table}}", table={
        "evidence_id": key, "columns": [{"field": "symbol", "label": "代码"}]})


def test_explicit_rank_then_filter_is_complete_only_within_that_scope():
    payload, args = source_fixture()
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=args)
    ranked = store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
    filtered = store.compute(Compute(evidence_id=ranked, filters=[
        {"field": "board_height", "operator": "eq", "value": 1}], limit=20))
    expected = [r for r in payload["events"][:20] if r["board_height"] == 1]
    assert store.get(filtered)["rows"] == expected
    assert render_answer(final(filtered), store).splitlines()[3:] == [f'| {r["symbol"]} |' for r in expected]
    for key in (root, ranked, filtered):
        assert store.view(key)["source_truncated"]  # Full universe remains incomplete.
        assert store.view(key)["result_state"] == "partial"
    assert store.complete_rank_scope(root) is None
    assert store.complete_rank_scope(filtered) == {
        "source_evidence_id": root, "sort_by": "amount", "descending": True, "offset": 0, "limit": 20}
    assert store.view(filtered)["complete_rank_scope"]["limit"] == 20
    with pytest.raises(ValueError, match="Truncated"):
        render_answer(final(root), store)


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_offset_and_ties_preserve_production_symbol_tie_breaker(direction):
    payload, args = source_fixture(direction=direction)
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=args)
    key = store.compute(Compute(evidence_id=root, sort_by="amount", descending=direction == "desc", offset=3, limit=5))
    assert store.get(key)["rows"] == payload["events"][3:8]
    assert store.complete_rank_scope(key)["offset"] == 3
    assert render_answer(final(key), store)


@pytest.mark.parametrize("change", [
    "unknown_tool", "explicit_truncation", "missing", "stale", "source_error", "partial_state",
    "wrong_count", "wrong_order", "wrong_arguments", "missing_sort", "duplicate_symbol", "wrong_date",
    "missing_value", "grouped", "multi_day", "forged_proof", "missing_date",
])
def test_length_or_untrusted_metadata_cannot_establish_rank_scope(change):
    payload, args = source_fixture()
    tool, state = "limit_up_events", "ok"
    if change == "unknown_tool": tool = "untrusted"
    if change == "explicit_truncation": payload["source_truncated"] = True
    if change == "missing": payload["data_missing"] = ["collection incomplete"]
    if change == "stale": payload["data_fresh"] = False
    if change == "source_error": payload["source_errors"] = ["provider failure"]
    if change == "partial_state": state = "partial"
    if change == "wrong_count": payload["returned_count"] -= 1
    if change == "wrong_order": payload["events"].reverse()
    if change == "wrong_arguments": args["sort_order"] = "asc"
    if change == "missing_sort": payload.pop("sort_by")
    if change == "duplicate_symbol": payload["events"][1]["symbol"] = payload["events"][0]["symbol"]
    if change == "wrong_date": payload["events"][1]["trade_date"] = "2026-09-18"
    if change == "missing_date":
        payload.pop("trade_date")
        for row in payload["events"]: row.pop("trade_date")
    if change == "missing_value": payload["events"][1]["amount"] = None
    if change == "grouped": payload["group_by"] = "industry"
    if change == "multi_day": payload["recent_trade_days"] = 2
    if change == "forged_proof":
        tool = "untrusted"
        payload["complete_rank_scope"] = {"limit": 20, "source_evidence_id": "fake"}
    store = EvidenceStore()
    root = store.add(tool=tool, payload=payload, state=state, arguments=args)
    if change == "missing_value":
        with pytest.raises(ValueError, match="Sort field missing"):
            store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
        return
    key = store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
    assert store.complete_rank_scope(key) is None
    with pytest.raises(ValueError, match="Truncated"):
        render_answer(final(key), store)


@pytest.mark.parametrize("spec", [
    {"sort_by": "amount", "descending": False, "limit": 20},
    {"sort_by": "board_height", "limit": 20},
    {"sort_by": "amount", "offset": 90, "limit": 20},
    {"sort_by": "amount", "limit": 20, "filters": [{"field": "board_height", "operator": "eq", "value": 1}]},
    {"operation": "aggregate"}, {"operation": "distinct"},
])
def test_global_filter_reorder_or_out_of_range_does_not_get_prefix_proof(spec):
    payload, args = source_fixture()
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=args)
    key = store.compute(Compute(evidence_id=root, **spec))
    assert store.complete_rank_scope(key) is None
    with pytest.raises(ValueError, match="Truncated"):
        render_answer(final(key), store)


def test_history_and_aggregation_do_not_launder_scope_proofs():
    payload, args = source_fixture()
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=args)
    ranked = store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
    aggregate = store.compute(Compute(evidence_id=ranked, operation="aggregate"))
    assert store.complete_rank_scope(aggregate) is None
    store.restore_history("old", deepcopy(store.get(ranked)))
    historical = store.compute(Compute(evidence_id="old", limit=10))
    assert store.complete_rank_scope(historical) is None
    with pytest.raises(ValueError, match="current-run"):
        render_answer(final(historical), store)
    with pytest.raises(ValueError, match="complete source sets"):
        store.compute(Compute(evidence_id=ranked, other_id=root, operation="difference"))


def test_empty_filter_is_a_complete_empty_subset_not_global_absence():
    payload, args = source_fixture()
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=args)
    ranked = store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
    empty = store.compute(Compute(evidence_id=ranked, filters=[
        {"field": "board_height", "operator": "eq", "value": 9}], limit=20))
    assert store.get(empty)["rows"] == []
    assert store.complete_rank_scope(empty)["limit"] == 20
    assert store.get(empty)["source_truncated"]
