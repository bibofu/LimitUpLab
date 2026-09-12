"""Evidence lineage and mathematical set semantics across chained operations."""

import pytest

from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore


def add(store, rows, **metadata):
    return store.add(tool="fixture", payload={"items": rows, **metadata},
                     state="ok" if rows else "empty", arguments={})


@pytest.mark.parametrize("metadata", [
    {"truncated": True}, {"source_truncated": True}, {"matched_count": 30},
    {"data_missing": ["second page unavailable"]}, {"data_fresh": False},
])
def test_incomplete_empty_is_not_proof_of_absence(metadata):
    store = EvidenceStore()
    key = add(store, [], **metadata)
    assert store.view(key)["result_state"] == "partial"
    other = add(store, [{"symbol": "000001"}])
    with pytest.raises(ValueError, match="complete source sets"):
        store.compute(Compute(evidence_id=other, operation="difference", other_id=key))


def test_chained_computation_preserves_missing_and_truncation():
    store = EvidenceStore()
    key = add(store, [{"symbol": "000001"}], matched_count=20,
              data_missing=["page 2 unavailable"])
    for operation in ("select", "distinct", "aggregate"):
        key = store.compute(Compute(evidence_id=key, operation=operation))
        view = store.view(key)
        assert view["result_state"] == "partial"
        assert view["source_truncated"]
        assert view["data_missing"] == ["page 2 unavailable"]


@pytest.mark.parametrize("operation", ["select", "distinct", "aggregate", "union", "intersection", "difference"])
def test_computation_cannot_refresh_historical_evidence(operation):
    store = EvidenceStore()
    old = add(store, [{"symbol": "000001"}])
    store.get(old)["historical_reference"] = True
    fresh = add(store, [{"symbol": "000002"}])
    key = store.compute(Compute(evidence_id=old, operation=operation, other_id=fresh))
    assert store.view(key)["historical_reference"]
    next_key = store.compute(Compute(evidence_id=key))
    assert store.view(next_key)["historical_reference"]


def test_right_hand_history_taints_derived_set():
    store = EvidenceStore()
    fresh = add(store, [{"symbol": "000001"}])
    old = add(store, [])
    store.get(old)["historical_reference"] = True
    key = store.compute(Compute(evidence_id=fresh, operation="difference", other_id=old))
    assert store.view(key)["historical_reference"]


@pytest.mark.parametrize(("operation", "expected"), [
    ("union", ["000001", "000002", "000003"]),
    ("intersection", ["000001"]), ("difference", ["000002"]),
    ("distinct", ["000001", "000002"]),
])
def test_sets_are_unique_and_keep_left_source_order(operation, expected):
    store = EvidenceStore()
    left = add(store, [{"symbol": s, "value": i} for i, s in enumerate(["000001", "000002", "000001"])])
    right = add(store, [{"symbol": "000001", "value": 99}, {"symbol": "000003"}])
    result = store.get(store.compute(Compute(evidence_id=left, operation=operation, other_id=right)))
    assert [row["symbol"] for row in result["rows"]] == expected
    if "000001" in expected:
        assert result["rows"][0]["value"] == 0


@pytest.mark.parametrize("invalid", [None, "", [], {}, True, float("nan")])
@pytest.mark.parametrize("operation", ["intersection", "difference", "union", "distinct"])
def test_invalid_set_keys_rejected_without_creating_evidence(invalid, operation):
    store = EvidenceStore()
    left = add(store, [{"symbol": invalid}])
    right = add(store, [])
    with pytest.raises(ValueError, match="Set key missing or invalid"):
        store.compute(Compute(evidence_id=left, operation=operation, other_id=right))
    assert len(store.records) == 2


def test_explicit_rank_slice_remains_a_usable_subset():
    store = EvidenceStore()
    key = add(store, [{"symbol": str(i), "score": i} for i in range(10)])
    selected = store.compute(Compute(evidence_id=key, sort_by="score", offset=3, limit=3))
    assert not store.view(selected)["source_truncated"]
    difference = store.compute(Compute(evidence_id=key, other_id=selected, operation="difference"))
    assert len(store.get(difference)["rows"]) == 7


def test_empty_count_is_zero_but_missing_source_count_remains_partial():
    store = EvidenceStore()
    empty = add(store, [])
    key = store.compute(Compute(evidence_id=empty, operation="aggregate"))
    assert store.get(key)["rows"] == [{"group": "all", "metric": "count", "value": 0}]
    assert store.get(key)["result_state"] == "ok"
    missing = add(store, [], data_missing=["source unavailable"])
    key = store.compute(Compute(evidence_id=missing, operation="aggregate"))
    assert store.get(key)["result_state"] == "partial"
