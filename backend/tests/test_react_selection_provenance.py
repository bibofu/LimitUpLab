"""Rank windows must come from actual computations, including after recovery."""

import json
from copy import deepcopy

import pytest

from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from test_react_rank_scope import final, source_fixture


def source():
    store = EvidenceStore()
    key = store.add(tool="fixture", state="ok", arguments={}, payload={"items": [
        {"symbol": f"60000{i}", "amount": 100 - i} for i in range(9)
    ]})
    return store, key


def test_wrong_window_and_wrong_sort_are_rejected_even_with_same_row_count():
    store, root = source()
    top = store.compute(Compute(evidence_id=root, sort_by="amount", limit=3))
    window = store.compute(Compute(evidence_id=root, sort_by="amount", offset=3, limit=3))
    assert not store.row_selection_matches(top, offset=3, limit=3)
    assert store.row_selection_matches(window, offset=3, limit=3, sort_by="amount", descending=True)
    assert store.row_selection_matches(window, offset=3, limit=3, sort_by="amount")
    assert not store.row_selection_matches(window, offset=3, limit=3, sort_by="amount", descending=False)
    assert not store.row_selection_matches(root, offset=3, limit=9)


def test_equivalent_chained_slice_is_verified_against_original_rank_offset():
    store, root = source()
    first = store.compute(Compute(evidence_id=root, sort_by="amount", offset=2, limit=5))
    second = store.compute(Compute(evidence_id=first, offset=1, limit=3))
    assert store.row_selection_matches(second, offset=3, limit=3, sort_by="amount", descending=True)
    assert not store.row_selection_matches(second, offset=1, limit=3)


@pytest.mark.parametrize("options", [{"sort_by": "amount", "descending": False},
                                      {"filters": [{"field": "amount", "operator": "lt", "value": 99}]}])
def test_capped_population_cannot_manufacture_a_new_global_window(options):
    store, root = source()
    first = store.compute(Compute(evidence_id=root, sort_by="amount", limit=5))
    altered = store.compute(Compute(evidence_id=first, offset=1, limit=2, **options))
    assert not store.row_selection_matches(altered, offset=1, limit=2)


def test_smaller_complete_population_is_allowed_but_smaller_arbitrary_slice_is_not():
    store, root = source()
    one = store.compute(Compute(evidence_id=root, limit=1))
    tail = store.compute(Compute(evidence_id=root, offset=8, limit=10))
    assert not store.row_selection_matches(one, offset=0, limit=3)
    assert store.row_selection_matches(tail, offset=8, limit=3)
    assert store.row_selection_matches(root, offset=0, limit=20)
    assert store.row_selection_matches(root, offset=0, limit=20, sort_by="amount", descending=True)


def test_payload_and_historical_claims_cannot_forge_runtime_selection_proofs():
    store, root = source()
    store.get(root)["payload"]["operation"] = {"offset": 3, "limit": 3}
    assert not store.row_selection_matches(root, offset=3, limit=9)
    selected = store.compute(Compute(evidence_id=root, offset=3, limit=3))
    store.restore_history("old", deepcopy(store.get(selected)))
    assert not store.row_selection_matches("old", offset=3, limit=3)


def test_checkpoint_preserves_rank_completeness_and_selection_proofs():
    payload, arguments = source_fixture()
    store = EvidenceStore()
    root = store.add(tool="limit_up_events", payload=payload, state="ok", arguments=arguments)
    key = store.compute(Compute(evidence_id=root, sort_by="amount", offset=3, limit=20))
    before = render_answer(final(key), store)
    restored = EvidenceStore()
    restored.restore_checkpoint(json.loads(json.dumps(store.checkpoint())))
    assert restored.complete_rank_scope(key) == store.complete_rank_scope(key)
    assert restored.row_selection_matches(key, offset=3, limit=20, sort_by="amount", descending=True)
    assert render_answer(final(key), restored) == before
    # Existing records-only snapshots stay readable but cannot self-assert proof.
    legacy = EvidenceStore()
    legacy.restore_checkpoint(json.loads(json.dumps(store.records)))
    assert legacy.complete_rank_scope(key) is None
    assert not legacy.row_selection_matches(key, offset=3, limit=20)


@pytest.mark.parametrize("operation", ["select", "distinct", "aggregate"])
@pytest.mark.parametrize("restore", [False, True])
def test_uncut_child_of_capped_ancestor_cannot_reset_the_ranking_population(operation, restore):
    store, root = source()
    first = store.compute(Compute(evidence_id=root, sort_by="amount", offset=3, limit=3))
    second = store.compute(Compute(evidence_id=first, operation=operation, limit=20))
    if restore:
        snapshot = json.loads(json.dumps(store.checkpoint()))
        store = EvidenceStore()
        store.restore_checkpoint(snapshot)
    field = "value" if operation == "aggregate" else "amount"
    # Neither the intermediate result nor a later compute may erase the cut.
    assert not store.row_selection_matches(second, offset=0, limit=3)
    third = store.compute(Compute(evidence_id=second, sort_by=field, descending=False, limit=3))
    assert not store.row_selection_matches(third, offset=0, limit=3, sort_by=field, descending=False)


def test_full_reorder_of_selected_window_does_not_fall_back_to_row_count():
    store, root = source()
    first = store.compute(Compute(evidence_id=root, sort_by="amount", limit=3))
    reversed_window = store.compute(Compute(evidence_id=first, sort_by="amount", descending=False, limit=3))
    assert not store.row_selection_matches(reversed_window, offset=0, limit=3,
                                           sort_by="amount", descending=False)


def test_filtering_a_selected_window_does_not_make_its_remaining_indices_global_ranks():
    store, root = source()
    first = store.compute(Compute(evidence_id=root, sort_by="amount", offset=2, limit=5))
    filtered = store.compute(Compute(evidence_id=first, filters=[
        {"field": "symbol", "operator": "in", "value": ["600002", "600004", "600006"]}], limit=20))
    assert store.row_selection_matches(filtered, offset=2, limit=5, sort_by="amount", descending=True)
    subset = store.compute(Compute(evidence_id=filtered, offset=1, limit=2))
    assert not store.row_selection_matches(subset, offset=3, limit=2)


@pytest.mark.parametrize("operation", ["select", "distinct", "aggregate"])
def test_complete_computed_population_can_establish_a_new_ranking(operation):
    store, root = source()
    complete = store.compute(Compute(evidence_id=root, operation=operation, limit=20))
    field = "value" if operation == "aggregate" else "amount"
    selected = store.compute(Compute(evidence_id=complete, sort_by=field, descending=False, limit=3))
    assert store.row_selection_matches(selected, offset=0, limit=3, sort_by=field, descending=False)


def test_noop_over_full_sorted_population_preserves_the_order_proof():
    store, root = source()
    full = store.compute(Compute(evidence_id=root, sort_by="amount", limit=20))
    noop = store.compute(Compute(evidence_id=full, limit=20))
    selected = store.compute(Compute(evidence_id=noop, offset=3, limit=3))
    assert store.row_selection_matches(selected, offset=3, limit=3, sort_by="amount", descending=True)


def test_unknown_truncated_population_cannot_prove_a_global_rank_window():
    store, root = source()
    store.get(root)["source_truncated"] = True
    selected = store.compute(Compute(evidence_id=root, sort_by="amount", offset=3, limit=3))
    assert not store.row_selection_matches(selected, offset=3, limit=3, sort_by="amount", descending=True)


def test_legacy_computed_rows_cannot_be_rebased_after_losing_provenance():
    store, root = source()
    first = store.compute(Compute(evidence_id=root, offset=3, limit=3))
    legacy = EvidenceStore()
    legacy.restore_checkpoint(json.loads(json.dumps(store.records)))
    assert not legacy.row_selection_matches(first, offset=0, limit=3)
    second = legacy.compute(Compute(evidence_id=first, sort_by="amount", limit=3))
    assert not legacy.row_selection_matches(second, offset=0, limit=3)
