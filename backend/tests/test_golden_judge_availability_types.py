"""Execution availability has a narrow citation type, not market truth semantics."""

from copy import deepcopy

import pytest

from evals.golden.judge_claims import aggregate_claims
from evals.golden.judge_references import EvidenceCatalog


SURFACES = {"final": "未返回任何可用行情数据。不能确认名单完整，不能排除还有遗漏。没有记录。"}
FIELD = "business_result_available"


@pytest.fixture
def payload():
    output = {"execution_status": "failed", "result_state": "error", "error_type": "RuntimeError",
        "error": "Tool execution failed; preserve other results and report missing evidence",
        "items": [], FIELD: False, "data_missing": ["tool_execution_failed"]}
    record = {"tool": "synthetic_query", "arguments": {"trade_date": "2026-09-22"},
        "result_state": "error", "evidence_scope": "current_run", "historical_reference": False,
        "payload": output, "rows": [], "data_missing": ["tool_execution_failed"]}
    observation = {"tool": record["tool"], "status": "error", "input": deepcopy(record["arguments"]),
        "output": deepcopy(output)}
    return {"synthetic_evidence": {"failed": record}, "business_observations": [observation],
        "trusted_runtime_metadata": [], "source_equivalence": []}


def flag_path(root):
    return ([root, "failed", "payload", FIELD] if root == "synthetic_evidence"
            else [root, 0, "output", FIELD])


def reference(payload, path):
    # Exercise the real server-owned catalog, including exact JSON value types.
    catalog = EvidenceCatalog.from_payload(payload)
    for group in catalog.wire_entries():
        for entry in group["entries"]:
            if [*group["prefix"], entry["key"]] == path:
                return catalog.resolve([{"ref_id": entry["ref_id"]}])[0]
    raise AssertionError("Test citation path does not exist")


def audit(payload, paths, *, target="query_status", relation="supported", quote=None):
    claim = {"surface_id": "final", "quote": quote or "未返回任何可用行情数据。", "target": target,
        "evidence_relation": relation, "evidence": [reference(payload, path) for path in paths]}
    return aggregate_claims([claim], dimension="factual", surfaces=SURFACES, payload=payload)


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_confirmed_runtime_failure_flag_is_typed_query_proof(payload, root, relation):
    result = audit(payload, [flag_path(root)], relation=relation)
    assert not result["validation_errors"]
    assert result["passed"] is (relation == "supported")
    assert result["claims"][0]["evidence_validation"] == [
        {"index": 0, "role": "query_status_proof", "validation_error": None}]
    # This verifies reference capability, not that both semantic relations are correct.
    assert audit(payload, [flag_path(root)], target="world_fact")["passed"] is None


def test_two_observed_failures_match_the_original_query_availability_claim(payload):
    second = deepcopy(payload["business_observations"][0])
    second["tool"] = "synthetic_alternate_query"
    payload["business_observations"].append(second)
    paths = [["business_observations", index, "output", field]
             for index in range(2) for field in (FIELD, "items")]
    result = audit(payload, paths)
    assert result["passed"] is True and not result["validation_errors"]
    assert audit(payload, paths, target="world_fact", quote="没有记录。")["passed"] is None


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("value", [True, 0, 1, "false", None, [], {}])
def test_flag_requires_the_runtime_false_boolean(payload, root, value):
    for output in (payload["synthetic_evidence"]["failed"]["payload"],
                   payload["business_observations"][0]["output"]):
        output[FIELD] = deepcopy(value)
    result = audit(payload, [flag_path(root)])
    assert result["validation_errors"] == ["claims[0]:QueryStatusRequiresQueryEvidence"]


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("mutation", ["no_failure", "unconfirmed", "uncertain", "nonempty_items",
                                      "missing_marker", "missing_items", "missing_type"])
def test_false_flag_cannot_self_attest_a_confirmed_failure(payload, root, mutation):
    outputs = [payload["synthetic_evidence"]["failed"]["payload"],
               payload["business_observations"][0]["output"]]
    for output in outputs:
        if mutation == "no_failure":
            output.update(execution_status="success", result_state="ok")
        elif mutation == "unconfirmed":
            output["execution_status"] = "unconfirmed"
        elif mutation == "uncertain":
            output["error_type"] = "UncertainExecution"
        elif mutation == "nonempty_items":
            output["items"] = [{"symbol": "SYNTHETIC"}]
        elif mutation == "missing_marker":
            output["data_missing"] = ["unrelated_gap"]
        elif mutation == "missing_items":
            del output["items"]
        else:
            del output["error_type"]
    assert audit(payload, [flag_path(root)])["validation_errors"] == [
        "claims[0]:QueryStatusRequiresQueryEvidence"]


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("wrapper", ["row", "nested"])
def test_same_named_business_fields_do_not_acquire_query_capability(payload, root, wrapper):
    path = flag_path(root)
    output = (payload["synthetic_evidence"]["failed"]["payload"] if root == "synthetic_evidence"
              else payload["business_observations"][0]["output"])
    if wrapper == "row":
        output["items"] = [{FIELD: False}]
        path = [*path[:-1], "items", 0, FIELD]
    else:
        output["details"] = {FIELD: False}
        path = [*path[:-1], "details", FIELD]
    assert audit(payload, [path])["validation_errors"] == ["claims[0]:QueryStatusRequiresQueryEvidence"]


@pytest.mark.parametrize("mutation", ["history", "unknown_scope", "missing_history_flag", "success_record",
                                      "missing_execution", "successful_execution", "wrong_tool",
                                      "wrong_arguments", "changed_output", "duplicate_record"])
def test_synthetic_flag_requires_one_current_record_bound_to_failed_execution(payload, mutation):
    record = payload["synthetic_evidence"]["failed"]
    observation = payload["business_observations"][0]
    if mutation == "history":
        record.update(evidence_scope="history", historical_reference=True)
    elif mutation == "unknown_scope":
        record.pop("evidence_scope")
    elif mutation == "missing_history_flag":
        record.pop("historical_reference")
    elif mutation == "success_record":
        record["result_state"] = "ok"
    elif mutation == "missing_execution":
        payload["business_observations"] = []
    elif mutation == "successful_execution":
        observation["status"] = "success"
    elif mutation == "wrong_tool":
        observation["tool"] = "different_query"
    elif mutation == "wrong_arguments":
        observation["input"]["trade_date"] = "2026-09-21"
    elif mutation == "changed_output":
        observation["output"]["extra"] = "different snapshot"
    else:
        payload["synthetic_evidence"]["duplicate"] = deepcopy(record)
    assert audit(payload, [flag_path("synthetic_evidence")])["validation_errors"] == [
        "claims[0]:QueryStatusRequiresQueryEvidence"]


def test_output_flag_requires_an_error_execution_observation(payload):
    payload["business_observations"][0]["status"] = "success"
    assert audit(payload, [flag_path("business_observations")])["passed"] is None


def test_record_rows_cannot_supply_runtime_availability_proof(payload):
    payload["synthetic_evidence"]["failed"]["rows"] = [{FIELD: False}]
    path = ["synthetic_evidence", "failed", "rows", 0, FIELD]
    assert audit(payload, [path])["validation_errors"] == ["claims[0]:QueryStatusRequiresQueryEvidence"]


@pytest.mark.parametrize("target", ["world_fact", "query_status"])
def test_malformed_flag_children_are_not_reinterpreted_as_business_proof(payload, target):
    observation = payload["business_observations"][0]
    observation.update(status="success", output={FIELD: {"arbitrary": False}})
    path = [*flag_path("business_observations"), "arbitrary"]
    assert audit(payload, [path], target=target)["passed"] is None


def test_untrusted_top_level_flag_is_not_reinterpreted_as_a_world_fact(payload):
    observation = payload["business_observations"][0]
    observation.update(status="success", output={FIELD: False})
    assert audit(payload, [flag_path("business_observations")], target="world_fact")[
        "validation_errors"] == ["claims[0]:AvailabilityIsNotWorldFact"]


def test_valid_availability_flag_does_not_exempt_unrelated_business_or_historical_proof(payload):
    good = flag_path("business_observations")
    payload["business_observations"][0]["output"]["price"] = 12.3
    bad = ["business_observations", 0, "output", "price"]
    result = audit(payload, [good, bad])
    assert result["validation_errors"] == ["claims[0]:QueryStatusRequiresQueryEvidence"]
    assert result["claims"][0]["evidence_validation"][0]["role"] == "query_status_proof"
    record = payload["synthetic_evidence"]["failed"]
    record.update(evidence_scope="history", historical_reference=True)
    assert audit(payload, [good, flag_path("synthetic_evidence")])["passed"] is None


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_query_uncertainty_remains_distinct_from_world_presence_or_absence(payload, relation):
    record = payload["synthetic_evidence"]["failed"]
    record.update(result_state="partial", data_missing=["source incomplete"],
                  payload={"items": [], "data_missing": ["source incomplete"]})
    paths = [["synthetic_evidence", "failed", "result_state"],
             ["synthetic_evidence", "failed", "data_missing", 0]]
    assert audit(payload, paths, quote="不能确认名单完整，不能排除还有遗漏。")["passed"] is True
    world = audit(payload, paths, target="world_fact", relation=relation, quote="没有记录。")
    assert world["validation_errors"] == ["claims[0]:AvailabilityIsNotWorldFact"]
    assert world["claims"][0]["reported_evidence_relation"] == relation
    assert world["claims"][0]["validated_evidence_relation"] is None
