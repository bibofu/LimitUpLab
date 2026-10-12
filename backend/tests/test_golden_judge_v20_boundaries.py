"""Real Judge resolver/aggregation contrasts; scripted claims are not model accuracy."""

from copy import deepcopy
import json

from langchain_core.messages import AIMessage
import pytest

from app.agents.react_runtime.contracts import Compute
from app.models import AgentChatResponse, AgentToolTrace
from evals.golden.calibration import load_calibration_cases
from evals.golden.judge import judge_turn
from golden_claim_fixture import catalog_entries


SOURCE = "synthetic-golden-world-v1"
TABLE = "| 代码 | 名称 |\n| --- | --- |\n| 000202 | 合成乙 |\n| 600101 | 合成甲 |"


def candidate(answer, *, state="ok", empty=False, metadata=None):
    rows = [] if empty else [{"symbol": "000202", "name": "合成乙", "amount": 8.0},
                            {"symbol": "600101", "name": "合成甲", "amount": 7.0}]
    arguments = {"symbol": "000202", "trade_date": "2026-09-22"}
    output = {"items": deepcopy(rows), "source": SOURCE, **(metadata or {})}
    record = {"tool": "synthetic_query", "arguments": arguments, "payload": output,
        "evidence_scope": "current_run", "historical_reference": False, "result_state": state,
        "source_truncated": False, "data_missing": ["query incomplete"] if state == "partial" else [],
        "rows": deepcopy(rows)}
    return AgentChatResponse(session_id="v20-typed-claims", intent="research", answer=answer,
        generated_by="scripted-protocol-fixture", tool_calls=["synthetic_query"], tool_results=[
            AgentToolTrace(name="synthetic_query", summary="Executed fixture query", status="success",
                input=deepcopy(arguments), output=deepcopy(output)),
            AgentToolTrace(name="react_execution", summary="Recorded fixture evidence",
                output={"evidence": {"data": record}})])


def data_path(*tail):
    return ["synthetic_evidence", "data", *tail]


def wire_ref(payload, path):
    entry = next(item for item in catalog_entries(payload) if item["path"] == path)
    return {"ref_id": entry["ref_id"]}


def finding(payload, *, quote, target="world_fact", relation="supported", paths=(), surface="final"):
    return {"surface_id": surface, "quote": quote, "target": target, "evidence_relation": relation,
            "evidence": [wire_ref(payload, path) for path in paths]}


class WireJudge:
    """Emit the exact requested fixture once, with no output adaptation or retry."""
    def __init__(self, build, dimension):
        self.build, self.dimension, self.calls, self.submitted = build, dimension, [], None

    def generate_messages(self, messages, tools, **kwargs):
        assert not self.calls, "A binding error must not silently trigger another Judge request"
        payload = json.loads(messages[-1].content)
        self.calls.append(payload)
        claims = self.build(payload)
        args = {key: {"reason": "Scripted type boundary; semantics were supplied by this test",
                     "claims": claims if key == self.dimension else []}
                for key in ("factual", "source", "safety")}
        self.submitted = deepcopy(args)
        return AIMessage(content="", tool_calls=[{"id": "v20-wire-decision",
            "name": tools[0]["function"]["name"], "args": args}])


def audit(response, build, *, dimension="factual", drafts=()):
    provider = WireJudge(build, dimension)
    review = judge_turn(provider, user="审查合成研究资料", expectations=(), response=response,
                        drafts=list(drafts))
    assert len(provider.calls) == 1
    return getattr(review, dimension), provider, review


@pytest.mark.parametrize("field", ["count_scope", "field_semantics", "filter_policy", "filter_audit", "metric_definitions"])
def test_query_semantics_are_scope_proof_not_standalone_world_proof(field):
    answer = "该查询口径只包含收盘封住的事件。"
    response = candidate(answer, metadata={field: "closed events only"})
    selected = data_path("payload", field)
    scoped, _, review = audit(response, lambda p: [finding(p, quote=answer, target="query_status", paths=[selected])])
    assert scoped["passed"] is True and not review.errors
    assert scoped["claims"][0]["evidence_validation"][0]["role"] == "query_semantics"
    world, _, _ = audit(response, lambda p: [finding(p, quote=answer, paths=[selected])])
    assert world["passed"] is None and world["validation_errors"] == ["claims[0]:AvailabilityIsNotWorldFact"]
    assert world["claims"][0]["target"] == "world_fact"
    assert world["claims"][0]["reported_evidence_relation"] == "supported"


@pytest.mark.parametrize("field", ["count_scope", "field_semantics", "filter_policy", "filter_audit", "metric_definitions"])
def test_business_leaf_can_use_query_semantics_as_auxiliary_context(field):
    answer = "合成乙的成交额为8，口径为收盘封住的事件。"
    response = candidate(answer, metadata={field: "closed events only"})
    checked, _, review = audit(response, lambda p: [finding(p, quote=answer, paths=[
        data_path("rows", 0, "amount"), data_path("payload", field)])])
    assert checked["passed"] is True and not review.errors
    assert [item["role"] for item in checked["claims"][0]["evidence_validation"]] == [
        "world_fact_proof", "auxiliary_availability_context"]


def test_pure_table_requires_business_leaves_not_only_compute_arguments():
    response = candidate(TABLE)
    evidence = response.tool_results[-1].output["evidence"]
    operation = Compute(evidence_id="data", sort_by="amount", limit=2).model_dump(mode="json")
    evidence["selected"] = {"tool": "compute_result", "arguments": deepcopy(operation),
        "evidence_scope": "current_run", "historical_reference": False, "result_state": "ok",
        "payload": {"operation": operation, "source_evidence_ids": ["data"], "items": deepcopy(evidence["data"]["rows"])},
        "rows": deepcopy(evidence["data"]["rows"])}
    paths = [["synthetic_evidence", "selected", "arguments", field] for field in ("limit", "sort_by", "descending")]
    invalid, _, _ = audit(response, lambda p: [finding(p, quote=TABLE, paths=paths)])
    assert invalid["validation_errors"] == ["claims[0]:AvailabilityIsNotWorldFact"]
    valid, _, review = audit(response, lambda p: [finding(p,
        quote=line, paths=[data_path("rows", index, field) for field in ("symbol", "name", "amount")])
        for index, line in enumerate(("| 000202 | 合成乙 |", "| 600101 | 合成甲 |"))])
    assert valid["passed"] is True and not review.errors
    assert len(valid["claims"]) == 2  # The test submits no invented execution assertion.


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_source_identity_stays_in_source_and_cannot_become_world_proof(relation):
    answer = "来源为合成评测数据。"
    paths = [["source_equivalence", 0, "source_id"], ["source_equivalence", 0, "identity_contract", "provenance_kind"]]
    response = candidate(answer)
    valid, _, review = audit(response, lambda p: [finding(p, quote=answer, target="source_attribution",
        relation=relation, paths=paths)], dimension="source")
    assert valid["passed"] is (relation == "supported") and not review.errors
    invalid, _, _ = audit(response, lambda p: [finding(p, quote=answer, relation=relation, paths=paths)])
    assert invalid["validation_errors"] == ["claims[0]:SourceIdentityIsNotWorldFact"]
    assert invalid["claims"][0]["reported_evidence_relation"] == relation


@pytest.mark.parametrize("relation", ["insufficient_evidence", "contradicted"])
def test_partial_empty_remains_valid_unknown_not_counterevidence(relation):
    answer = "合成乙在2026-09-22没有记录。"
    response = candidate(answer, state="partial", empty=True)
    checked, _, _ = audit(response, lambda p: [finding(p, quote=answer, relation=relation, paths=[
        data_path("payload", "items"), data_path("result_state"), data_path("data_missing")])])
    assert checked["passed"] is None
    if relation == "insufficient_evidence":
        assert checked["validation_errors"] == []
        assert checked["claims"][0]["validated_evidence_relation"] == relation
    else:
        assert checked["validation_errors"] == ["claims[0]:IncompleteEmptyCollectionIsNotWorldAbsence"]
        assert checked["claims"][0]["reported_evidence_relation"] == "contradicted"
        assert checked["claims"][0]["validated_evidence_relation"] is None


@pytest.mark.parametrize("case_id", ["wrong_entity", "wrong_date"])
@pytest.mark.parametrize("reference_kind", ["none", "result_state"])
def test_wrong_object_or_date_cannot_be_disproved_by_empty_or_status_only_evidence(case_id, reference_kind):
    case = next(item for item in load_calibration_cases() if item.id == case_id)
    paths = [] if reference_kind == "none" else [["synthetic_evidence", "ev_calibration", "result_state"]]
    # Current evidence belongs to another entity/date. We deliberately do not
    # supply a business value and pretend the validator can infer entailment.
    checked, _, _ = audit(case.response(), lambda p: [finding(p, quote=case.answer,
        relation="contradicted", paths=paths)])
    error = "MissingClaimEvidence" if reference_kind == "none" else "AvailabilityIsNotWorldFact"
    assert checked["validation_errors"] == ["claims[0]:" + error]
    unknown, _, _ = audit(case.response(), lambda p: [finding(p, quote=case.answer,
        relation="insufficient_evidence", paths=paths)])
    assert unknown["passed"] is None and not unknown["validation_errors"]


def test_complete_scoped_empty_is_allowed_without_inventing_missing_records():
    answer = "合成乙在2026-09-22没有记录。"
    response = candidate(answer, state="empty", empty=True)
    checked, _, review = audit(response, lambda p: [finding(p, quote=answer, paths=[data_path("payload", "items")])])
    assert checked["passed"] is True and not review.errors
    note = checked["claims"][0]["evidence_validation"][0]
    assert note["role"] == "scoped_empty_result" and note["scope_limited"] is True
    assert note["query_scope"] == {"symbol": "000202", "trade_date": "2026-09-22"}


@pytest.mark.parametrize("mutation", ["substitute", "delete"])
def test_one_character_wrong_reference_is_preserved_and_never_repaired_or_dropped(mutation):
    answer = "来源为合成评测数据。"
    def build(payload):
        item = finding(payload, quote=answer, target="source_attribution", paths=[
            ["source_equivalence", 0, "source_id"], ["source_equivalence", 0, "identity_contract", "provenance_kind"]])
        original = item["evidence"][1]["ref_id"]
        changed = ("A" if original[0] != "A" else "B") + original[1:] if mutation == "substitute" else original[:7] + original[8:]
        assert changed not in {entry["ref_id"] for entry in catalog_entries(payload)}
        item["evidence"][1]["ref_id"] = changed
        return [item]
    checked, provider, _ = audit(candidate(answer), build, dimension="source")
    # A bad reference must not be rescued by the other valid source reference.
    assert checked is not None
    assert checked["passed"] is None and checked["validation_errors"] == ["claims[0]:UnknownEvidenceReference"]
    assert checked["claims"][0]["reported_evidence"] == provider.submitted["source"]["claims"][0]["evidence"]
    assert checked["claims"][0]["reported_evidence_relation"] == "supported"
    assert checked["claims"][0]["validated_evidence_relation"] is None


def test_wrong_dimension_target_is_not_moved_or_accepted():
    answer = "来源为合成评测数据。"
    checked, provider, review = audit(candidate(answer), lambda p: [finding(p, quote=answer,
        target="source_attribution", paths=[["source_equivalence", 0, "source_id"]])])
    assert checked["claims"][0]["target"] == "source_attribution"
    assert checked["claims"][0]["quote"] == answer
    assert checked["validation_errors"] == ["claims[0]:ClaimTargetDimensionMismatch"]
    assert review.source["claims"] == [] and review.source["passed"] is True
    assert provider.submitted["factual"]["claims"][0]["target"] == "source_attribution"


def test_correct_final_does_not_drop_distinct_wrong_draft():
    final, draft = "合成乙成交额为8。", "合成乙成交额为99。"
    checked, provider, _ = audit(candidate(final), lambda p: [
        finding(p, quote=final, paths=[data_path("rows", 0, "amount")]),
        finding(p, quote=draft, relation="contradicted", surface="draft_0", paths=[data_path("rows", 0, "amount")])], drafts=[draft])
    assert checked["passed"] is False and not checked["validation_errors"]
    assert provider.calls[0]["surfaces"] == [{"surface_id": "final", "text": final}, {"surface_id": "draft_0", "text": draft}]
    assert checked["claims"][1]["surface_id"] == "draft_0"
