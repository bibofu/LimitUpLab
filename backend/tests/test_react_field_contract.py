"""Field-contract regressions using synthetic protocol data, never a live model."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.runtime import Run
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatRequest, FirstBoardRating, LimitUpEvent
from app.services.prompt_security import review_input


TASK = "查询合成研究名单，只列代码、名称和板数。"


class Reviewer:
    def __init__(self, fields=None, *, mappings=None):
        self.fields, self.mappings, self.schema = fields, mappings, None

    def generate_messages(self, messages, tools, **kwargs):
        self.schema = tools[0]["function"]["parameters"]
        contract = {"mode": "table_only", "table_required": True}
        contract.update({"field_requests": self.mappings} if self.mappings is not None else {"fields": self.fields})
        return AIMessage(content="", tool_calls=[{"name": "submit_input_security_review", "id": "review",
            "args": {"decision": "allow", "signals": [], "reason": "scripted semantic interpretation",
                "request_kind": "research", "context_mode": "standalone", "current_task": TASK,
                "output_contract": contract}}])


def review(fields):
    provider = Reviewer(fields)
    assessment = review_input(provider, message=TASK, timeout_seconds=10)
    return assessment, provider.schema


def field_items(schema):
    node = output_contract_schema(schema)["properties"]["field_requests"]["items"]
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].split("/")[-1]]
    return node["properties"]["field"]


def output_contract_schema(schema):
    node = schema["properties"]["output_contract"]
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].split("/")[-1]]
    return node


def resolution_records(value):
    """Audit shape may be nested, but original proposals and a reason must survive."""
    if isinstance(value, dict):
        if "proposed_fields" in value:
            yield value
        for child in value.values():
            yield from resolution_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from resolution_records(child)


def test_review_schema_offers_trusted_raw_field_enum_instead_of_unbounded_strings():
    _, schema = review(["symbol", "name", "board_height"])
    allowed = next(choice["enum"] for choice in field_items(schema)["anyOf"] if "enum" in choice)
    assert {"symbol", "name", "board_height", "break_count", "score"} <= set(allowed)
    assert "板数" not in allowed and "board_heigth" not in allowed
    assert "field_resolution" not in output_contract_schema(schema)["properties"]
    assert "fields" not in output_contract_schema(schema)["properties"]


def test_catalog_fields_are_scalars_in_the_declared_production_models():
    models = {model.__name__: model for model in (LimitUpEvent, FirstBoardRating)}
    for key, entry in DISPLAY_FIELD_CATALOG.items():
        declaration = models[entry["model"]].model_json_schema()["properties"][key]
        assert declaration.get("type") in {"string", "integer", "number", "boolean"}
        assert entry["meaning"] and entry["tools"]


@pytest.mark.parametrize("proposed", [
    ["symbol", "name", "板数"],
    ["symbol", "name", "board_heigth"],
    ["symbol", "name", "made_up_research_metric"],
    ["symbol", "name", "symbol"],
])
def test_invalid_or_duplicate_field_makes_the_whole_restriction_unresolved(proposed):
    assessment, _ = review(proposed)
    assert assessment.output_contract.fields == [], "Never silently retain only a valid subset"
    assert assessment.current_task == TASK
    assert assessment.output_contract.mode == "table_only" and assessment.output_contract.table_required
    audits = list(resolution_records(assessment.model_dump(mode="json")))
    assert any(item["proposed_fields"] == proposed and item.get("reason") for item in audits)


@pytest.mark.parametrize("metric", ["board_height", "score"])
def test_trusted_fields_still_enforce_exact_columns_and_order(metric):
    fields = ["symbol", "name", metric]
    assessment, _ = review(fields)
    assert assessment.output_contract.fields == fields
    good = Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": "test",
        "columns": [{"field": field, "label": field} for field in fields]})
    validate_delivery(good, assessment)
    for wrong in (fields[:2], list(reversed(fields)), [*fields, "industry"]):
        altered = good.model_copy(update={"table": good.table.model_copy(update={
            "columns": [good.table.columns[0].model_copy(update={"field": field}) for field in wrong]})})
        with pytest.raises(ValueError, match="Table fields"):
            validate_delivery(altered, assessment)


def test_old_checkpoint_unknown_field_cannot_poison_recovered_delivery():
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    runtime = Run(AgentChatRequest(session_id="field-contract", message=TASK), registry, object(), [], None)
    old_task = {"current_task": TASK, "pending_slots": [], "output_contract": {
        "mode": "table_only", "fields": ["symbol", "name", "板数"], "table_required": True}}
    runtime.control = SimpleNamespace(row={"checkpoint_json": json.dumps({
        "runtime": {"task_context": old_task}, "state": {"messages": [], "resume_node": "agent"}})})
    runtime.restore()
    assert runtime.task_context.current_task == TASK
    assert runtime.task_context.output_contract.fields == []
    assert any(record["proposed_fields"] == old_task["output_contract"]["fields"] and record.get("reason")
               for record in resolution_records(runtime.task_context.model_dump(mode="json")))
    restored_again = TaskInterpretation.model_validate(runtime.task_context.model_dump(mode="json"))
    assert restored_again.output_contract.fields == []
    assert any(record["proposed_fields"] == old_task["output_contract"]["fields"] and record.get("reason")
               for record in resolution_records(restored_again.model_dump(mode="json")))
    validate_delivery(Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": "test",
        "columns": [{"field": field, "label": field} for field in ["symbol", "name", "board_height"]]}),
        runtime.task_context)


def test_backend_does_not_trust_a_model_claim_that_unknown_fields_are_resolved():
    proposed = ["symbol", "name", "not_a_real_field"]
    interpreted = TaskInterpretation.model_validate({"current_task": TASK, "output_contract": {
        "mode": "table_only", "fields": proposed, "table_required": True,
        "field_resolution": {"status": "resolved", "proposed_fields": ["symbol", "name"], "reason": "model says so"}}})
    assert interpreted.output_contract.fields == []
    audit = next(resolution_records(interpreted.model_dump(mode="json")))
    assert audit["proposed_fields"] == proposed and audit["status"] == "unresolved" and audit["reason"]


def test_unresolved_field_contract_does_not_create_missing_evidence_fields():
    store = EvidenceStore()
    evidence_id = store.add(tool="synthetic", state="ok", arguments={}, payload={
        "items": [{"symbol": "600123", "name": "回归甲"}]})
    final = Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": evidence_id,
        "columns": [{"field": field, "label": field} for field in ["symbol", "name", "board_height"]]})
    validate_delivery(final, TaskInterpretation(current_task=TASK,
        output_contract={"mode": "table_only", "fields": [], "table_required": True}))
    with pytest.raises(ValueError, match="Table field missing"):
        render_answer(final, store)


@pytest.mark.parametrize("unbound", [None, "板数", "not_a_real_field", "symbol"])
def test_mapping_preserves_unbound_third_request_without_locking_valid_prefix(unbound):
    mappings = [{"requested": "代码", "field": "symbol"}, {"requested": "名称", "field": "name"},
                {"requested": "板数", "field": unbound}]
    assessment = review_input(Reviewer(mappings=mappings), message=TASK, timeout_seconds=10)
    assert assessment.current_task == TASK
    assert assessment.output_contract.fields == []
    assert [item.model_dump() for item in assessment.output_contract.field_requests] == mappings
    audit = assessment.output_contract.field_resolution
    assert audit.status == "unresolved" and audit.proposed_fields == ["symbol", "name", unbound]
    assert assessment.output_contract.mode == "table_only" and assessment.output_contract.table_required
    restored = TaskInterpretation.model_validate({"current_task": TASK,
        "output_contract": assessment.output_contract.model_dump(mode="json")})
    assert restored.output_contract == assessment.output_contract


def test_complete_mapping_derives_all_requested_hard_fields_and_ignores_legacy_conflict():
    mappings = [{"requested": "代码", "field": "symbol"}, {"requested": "名称", "field": "name"},
                {"requested": "开板次数", "field": "break_count"}]
    assessment = review_input(Reviewer(mappings=mappings), message="只列代码名称开板次数。", timeout_seconds=10)
    assert assessment.output_contract.fields == ["symbol", "name", "break_count"]
    legacy_conflict = TaskInterpretation.model_validate({"output_contract": {
        "fields": ["symbol", "name"], "field_requests": mappings}})
    assert legacy_conflict.output_contract.fields == ["symbol", "name", "break_count"]
    for fields in (["symbol", "name"], ["symbol", "name", "break_count", "industry"]):
        with pytest.raises(ValueError, match="Table fields"):
            validate_delivery(Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": "test",
                "columns": [{"field": field, "label": field} for field in fields]}), assessment)


def test_diagnostics_alone_cannot_create_a_hard_binding():
    task = TaskInterpretation.model_validate({"output_contract": {
        "field_resolution": {"status": "resolved", "proposed_fields": ["symbol", "name"], "reason": "claimed"}}})
    assert task.output_contract.fields == [] and task.output_contract.field_resolution.status == "unresolved"


def test_mapping_does_not_claim_to_prove_semantic_completeness():
    # The model can omit a requested item before binding. No deterministic parser
    # can prove its meaning from current_task; real-model golden oracles must test it.
    task = TaskInterpretation.model_validate({"current_task": TASK, "output_contract": {
        "field_requests": [{"requested": "代码", "field": "symbol"}, {"requested": "名称", "field": "name"}]}})
    assert task.current_task == TASK and task.output_contract.fields == ["symbol", "name"]
