"""Deterministic completion gates; no provider or market service is invoked."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agents.react_runtime.contracts import Finish, UpdateTask
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.task_contract import (
    RowSelection, TaskInterpretation, update_requirements, validate_delivery,
    validate_requirements,
)


def requirement(identifier="list", **changes):
    return {"id": identifier, "description": "返回所请求的研究名单", "source_text": "列出名单",
            "status": "pending", "evidence_ids": [], **changes}


def evidence(state="ok", *, history=False):
    store = EvidenceStore()
    key = store.add(tool="synthetic", arguments={}, state=state,
                    payload={"items": [] if state == "empty" else [{"symbol": "600001"}]})
    if history:
        store.restore_history(key, store.get(key))
    return store, key


@pytest.mark.parametrize("changes", [{"id": " "}, {"description": "\n"},
                                     {"source_text": ""}, {"evidence_ids": [" "]}])
def test_blank_requirement_identity_or_source_is_rejected(changes):
    with pytest.raises(ValidationError):
        UpdateTask(requirements=[requirement(**changes)])


def test_duplicate_ids_cannot_overwrite_a_prior_requirement():
    first = requirement()
    with pytest.raises(ValidationError, match="IDs must be unique"):
        update_requirements([first], [first, requirement(status="unavailable")])


@pytest.mark.parametrize("field", ["description", "source_text"])
def test_updates_cannot_relax_or_rewrite_recorded_obligations(field):
    original = [requirement()]
    saved = deepcopy(original)
    with pytest.raises(ValueError, match="Cannot rewrite"):
        update_requirements(original, [requirement(**{field: "只需返回第一项"})])
    assert original == saved


def test_updates_cannot_drop_an_existing_id():
    with pytest.raises(ValueError, match="Cannot remove"):
        update_requirements([requirement()], [requirement("count")])


def test_update_can_append_and_satisfy_items_without_mutating_prior_state():
    store, key = evidence()
    original = [requirement()]
    result = update_requirements(original, [requirement(status="satisfied", evidence_ids=[key]),
                                           requirement("count")], store)
    assert result[0]["status"] == "satisfied" and result[1]["id"] == "count"
    assert original == [requirement()]


@pytest.mark.parametrize("status", ["pending", "unavailable"])
def test_complete_cannot_ignore_an_unfinished_registered_requirement(status):
    with pytest.raises(ValueError, match="remain pending or unavailable"):
        validate_requirements([requirement(status=status)], "complete")
    validate_requirements([requirement(status=status)], "partial")
    validate_requirements([requirement(status=status)], "clarify")


@pytest.mark.parametrize("state", ["ok", "empty", "partial"])
def test_satisfied_research_requirement_accepts_usable_current_evidence(state):
    store, key = evidence(state)
    validate_requirements([requirement(status="satisfied", evidence_ids=[key])], "complete", store)


@pytest.mark.parametrize("state,history", [("error", False), ("ok", True), ("empty", True)])
def test_satisfied_cannot_use_error_or_previous_turn_evidence(state, history):
    store, key = evidence(state, history=history)
    with pytest.raises(ValueError, match="usable current-run evidence"):
        update_requirements([], [requirement(status="satisfied", evidence_ids=[key])], store)


def test_every_satisfied_reference_is_checked_not_only_one_good_reference():
    store, good = evidence()
    bad = store.add(tool="synthetic", state="error", arguments={}, payload={})
    with pytest.raises(ValueError, match="usable current-run evidence"):
        validate_requirements([requirement(status="satisfied", evidence_ids=[good, bad])],
                              "complete", store)
    with pytest.raises(ValueError, match="Unknown evidence_id"):
        validate_requirements([requirement(status="satisfied", evidence_ids=["ev_invented"])],
                              "complete", store)


def test_research_requires_evidence_but_simple_turns_need_no_checklist():
    with pytest.raises(ValueError, match="requires current usable evidence"):
        validate_requirements([requirement(status="satisfied")], "complete")
    validate_requirements([], "complete")


def test_conversation_can_satisfy_without_business_evidence_but_not_fake_references():
    items = [requirement(status="satisfied")]
    assert update_requirements([], items, requires_current_evidence=False) == items
    validate_requirements(items, "complete", requires_current_evidence=False)
    with pytest.raises(ValueError, match="requires the current evidence store"):
        validate_requirements([requirement(status="satisfied", evidence_ids=["ev_fake"])],
                              "complete", requires_current_evidence=False)


def field_task(unbound=None):
    return TaskInterpretation(output_contract={"table_required": True, "field_requests": [
        {"requested": "代码", "field": "symbol"}, {"requested": "名称", "field": "name"},
        {"requested": "待明确指标", "field": unbound}]})


def answer(fields=("symbol", "name"), *, status="partial"):
    return Finish(status=status, answer="{{evidence_table}}", missing=["待明确指标"],
                  table={"evidence_id": "ev_rows", "columns": [
                      {"field": field, "label": field} for field in fields]})


@pytest.mark.parametrize("unbound", [None, "板数", "not_a_field", "symbol"])
def test_unresolved_requested_field_prevents_complete_and_survives_checkpoint(unbound):
    task = field_task(unbound)
    restored = TaskInterpretation.model_validate_json(task.model_dump_json())
    assert restored == task and restored.output_contract.fields == []
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(answer(status="complete"), restored)
    validate_delivery(answer(), restored)


@pytest.mark.parametrize("fields", [("symbol",), ("name", "symbol"),
                                    ("symbol", "name", "industry")])
def test_unresolved_field_does_not_erase_known_columns_or_their_order(fields):
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(answer(fields), field_task())


def test_legacy_unresolved_fields_keep_real_request_origin_across_roundtrips():
    task = TaskInterpretation(output_contract={"fields": ["symbol", "name", "unknown"]})
    for _ in range(2):
        task = TaskInterpretation.model_validate_json(task.model_dump_json())
    assert [item.field for item in task.output_contract.field_requests] == ["symbol", "name", "unknown"]
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(answer(status="complete"), task)
    validate_delivery(answer(), task)


def test_diagnostic_alone_does_not_create_a_request_or_block_complete():
    task = TaskInterpretation(output_contract={"field_resolution": {
        "status": "unresolved", "proposed_fields": ["unknown"], "reason": "model claim"}})
    validate_delivery(Finish(status="complete", answer="普通对话。"), task)


def test_row_window_and_explicit_sort_are_checked_against_store_proof():
    class Store:
        calls = []
        proven = False

        def get(self, key):
            return {"rows": [{"symbol": "600001"}]}

        def row_selection_matches(self, key, **selection):
            self.calls.append((key, selection))
            return self.proven

    store = Store()
    task = TaskInterpretation(output_contract={"row_selection": {
        "requested": "成交额降序第4至6名", "offset": 3, "limit": 3,
        "sort_by": "amount", "descending": True}})
    with pytest.raises(ValueError, match="does not prove the requested row window"):
        validate_delivery(answer(), task, store)
    assert store.calls == [("ev_rows", {"offset": 3, "limit": 3,
                                        "sort_by": "amount", "descending": True})]
    store.proven = True
    validate_delivery(answer(), task, store)


@pytest.mark.parametrize("sort", [{"sort_by": ""}, {"descending": "false"}, {"descending": 1}])
def test_sort_declaration_has_strict_types(sort):
    with pytest.raises(ValidationError):
        RowSelection(requested="前两名", limit=2, **sort)
