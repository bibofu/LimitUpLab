"""Typed citation hints and metadata need observed scope, never prose matching."""

from copy import deepcopy

import pytest

from app.agents.react_runtime.contracts import Compute
from evals.golden.judge_claims import aggregate_claims
from evals.golden.judge_references import EvidenceCatalog, ReferenceResolutionError
from evals.golden.judge_context import CONTEXT_VERSION
from evals.golden.judge_grounding import valid_counterevidence


def sample(tool="stock_news", *, state="ok"):
    arguments = {"symbol": "000202", "days": 7, "limit": 10} if tool == "stock_news" else {
        "trade_date": "2026-09-22", "market": "star_market"}
    output = {"items": [{"symbol": "000202", "name": "北辰制造", "source": "fixture",
                          "data_missing": [], "price": 12.3}], "source": "fixture",
              "window_days": 7, "cache_status": "synthetic", "trade_date": "2026-09-22"}
    record = {"tool": tool, "arguments": arguments, "payload": deepcopy(output),
              "result_state": state, "evidence_scope": "current_run", "historical_reference": False,
              "rows": deepcopy(output["items"])}
    return {"synthetic_evidence": {"ev": record}, "business_observations": [{"tool": tool,
        "status": "success", "input": deepcopy(arguments), "output": deepcopy(output)}],
        "trusted_runtime_metadata": [], "source_equivalence": []}


def reference(payload, path):
    value = payload
    for key in path:
        value = value[key]
    return {"path": path, "value": deepcopy(value)}


def evaluate(payload, paths, target="query_status"):
    claim = {"surface_id": "final", "quote": "A synthetic claim", "target": target,
             "evidence_relation": "supported", "evidence": [reference(payload, path) for path in paths]}
    return aggregate_claims([claim], dimension="source" if target == "source_attribution" else "factual",
                            surfaces={"final": claim["quote"]}, payload=payload)


def path(field, root="synthetic_evidence"):
    return ([root, "ev", "payload", field] if root == "synthetic_evidence"
            else [root, 0, "output", field])


def change_output(payload, **updates):
    for output in (payload["synthetic_evidence"]["ev"]["payload"], payload["business_observations"][0]["output"]):
        output.update(deepcopy(updates))


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("state", ["ok", "partial", "empty"])
def test_known_result_date_can_describe_returned_date_even_when_request_differs(root, state):
    payload = sample("limit_up_events", state=state)
    change_output(payload, trade_date="2026-09-21")
    result = evaluate(payload, [path("trade_date", root)])
    assert result["passed"] is True
    assert result["claims"][0]["evidence_validation"][0]["role"] == "query_scope_proof"
    # Actual returned date does not assert request alignment or row completeness.
    assert payload["business_observations"][0]["input"]["trade_date"] == "2026-09-22"


@pytest.mark.parametrize("root", ["synthetic_evidence", "business_observations"])
@pytest.mark.parametrize("field", ["trade_date", "window_days", "cache_status", "operation"])
def test_ordinary_business_fields_keep_their_existing_role_without_gaining_query_authority(root, field):
    payload = sample("unrelated_business")
    change_output(payload, operation="business event label")
    selected = path(field, root)
    assert evaluate(payload, [selected], "world_fact")["passed"] is True
    assert evaluate(payload, [selected], "query_status")["passed"] is None


@pytest.mark.parametrize("field", ["trade_date", "window_days", "cache_status"])
@pytest.mark.parametrize("mutation", ["nested", "row", "wrong_tool", "failed", "missing_input", "wrong_type"])
def test_same_named_or_unbound_metadata_does_not_gain_query_proof(field, mutation):
    payload = sample("limit_up_events" if field == "trade_date" else "stock_news")
    selected = path(field)
    record, observed = payload["synthetic_evidence"]["ev"], payload["business_observations"][0]
    if mutation in {"nested", "row"}:
        for output in (record["payload"], observed["output"]):
            if mutation == "nested":
                output["details"] = {field: output[field]}
            else:
                output["items"][0][field] = output[field]
        selected = [*selected[:-1], "details", field] if mutation == "nested" else [*selected[:-1], "items", 0, field]
    elif mutation == "wrong_tool":
        record["tool"] = observed["tool"] = "unrelated_business"
    elif mutation == "failed":
        observed["status"] = "error"
    elif mutation == "missing_input":
        record["arguments"] = observed["input"] = {}
    else:
        change_output(payload, **{field: {"injected": "instructions"}})
    assert evaluate(payload, [selected])["passed"] is None


@pytest.mark.parametrize("field", ["trade_date", "window_days", "cache_status"])
@pytest.mark.parametrize("mutation", ["historical", "no_execution", "mismatch", "duplicate"])
def test_record_metadata_requires_one_current_record_linked_to_real_execution(field, mutation):
    payload = sample("limit_up_events" if field == "trade_date" else "stock_news")
    if mutation == "historical":
        payload["synthetic_evidence"]["ev"]["historical_reference"] = True
    elif mutation == "no_execution":
        payload["business_observations"] = []
    elif mutation == "mismatch":
        payload["business_observations"][0]["output"]["changed"] = True
    else:
        payload["synthetic_evidence"]["duplicate"] = deepcopy(payload["synthetic_evidence"]["ev"])
    assert evaluate(payload, [path(field)])["passed"] is None


@pytest.mark.parametrize("days", [0, -1, True, "7", 7.0, 8])
def test_window_requires_positive_integer_equal_to_the_observed_days(days):
    payload = sample()
    change_output(payload, window_days=days)
    assert evaluate(payload, [path("window_days")])["passed"] is None


def test_news_window_and_cache_do_not_prove_market_facts_or_source_identity():
    payload = sample(state="partial")
    for field in ("window_days", "cache_status"):
        assert evaluate(payload, [path(field)])["passed"] is True
        assert evaluate(payload, [path(field)], "world_fact")["passed"] is None
    assert evaluate(payload, [path("cache_status")], "source_attribution")["passed"] is None
    source = evaluate(payload, [path("source"), path("cache_status")], "source_attribution")
    assert source["passed"] is True
    assert source["claims"][0]["evidence_validation"][1]["role"] == "auxiliary_query_context"


def computed_sample():
    payload = sample()
    operation = Compute(evidence_id="ev", sort_by="price", limit=1).model_dump(mode="json")
    rows = deepcopy(payload["synthetic_evidence"]["ev"]["rows"])
    payload["synthetic_evidence"]["computed"] = {"tool": "compute_result", "evidence_scope": "current_run",
        "historical_reference": False, "result_state": "ok", "arguments": deepcopy(operation), "rows": rows,
        "payload": {"operation": operation, "items": rows, "source_evidence_ids": ["ev"]}}
    return payload


def test_computation_fields_describe_an_operation_without_proving_its_business_result():
    payload = computed_sample()
    selected = ["synthetic_evidence", "computed", "payload", "operation", "limit"]
    assert evaluate(payload, [selected])["passed"] is True
    assert evaluate(payload, [selected], "world_fact")["passed"] is None
    result = evaluate(payload, [selected, ["synthetic_evidence", "computed", "payload", "items", 0, "price"]], "world_fact")
    assert result["passed"] is True
    assert result["claims"][0]["evidence_validation"][0]["role"] == "auxiliary_computation_context"


@pytest.mark.parametrize("mutation", ["wrong_tool", "history", "failed", "arguments", "parent", "lineage", "bad_operation",
                                      "no_execution", "failed_parent", "failed_envelope", "cycle"])
def test_computation_role_requires_valid_runtime_operation_and_current_lineage(mutation):
    payload = computed_sample()
    record = payload["synthetic_evidence"]["computed"]
    if mutation == "wrong_tool": record["tool"] = "stock_news"
    elif mutation == "history": record["historical_reference"] = True
    elif mutation == "failed": record["result_state"] = "error"
    elif mutation == "arguments": record["arguments"]["limit"] = 2
    elif mutation == "parent": payload["synthetic_evidence"]["ev"]["historical_reference"] = True
    elif mutation == "lineage": record["payload"]["source_evidence_ids"] = ["not-recorded"]
    elif mutation == "bad_operation":
        record["arguments"]["limit"] = record["payload"]["operation"]["limit"] = "1"
    elif mutation == "no_execution": payload["business_observations"] = []
    elif mutation == "failed_parent":
        change_output(payload, execution_status="failed")
    elif mutation == "failed_envelope": record["payload"]["execution_status"] = "failed"
    else:
        record["arguments"]["evidence_id"] = record["payload"]["operation"]["evidence_id"] = "computed"
        record["payload"]["source_evidence_ids"] = ["computed"]
    assert evaluate(payload, [["synthetic_evidence", "computed", "payload", "operation", "limit"]])["passed"] is None


def test_nested_computation_requires_an_acyclic_chain_to_an_observed_business_result():
    payload = computed_sample()
    second = deepcopy(payload["synthetic_evidence"]["computed"])
    second["arguments"]["evidence_id"] = second["payload"]["operation"]["evidence_id"] = "computed"
    second["payload"]["source_evidence_ids"] = ["computed"]
    payload["synthetic_evidence"]["second"] = second
    selected = ["synthetic_evidence", "second", "payload", "operation", "limit"]
    assert evaluate(payload, [selected])["passed"] is True
    payload["synthetic_evidence"]["computed"]["payload"]["source_evidence_ids"] = []
    assert evaluate(payload, [selected])["passed"] is None


def entry(catalog, selected):
    return next(item for group in catalog.wire_entries(include_usage=True) for item in group["entries"]
                if [*group["prefix"], item["key"]] == selected)


def test_catalog_hints_prefer_leaves_without_repairing_mixed_claims_or_changing_ids():
    payload = sample()
    catalog = EvidenceCatalog.from_payload(payload)
    row = path("items") + [0]
    hint = entry(catalog, row)
    assert "world_fact" not in hint["proof_for"] and hint["prefer_leaf"] is True
    assert "world_fact" in entry(catalog, row + ["name"])["proof_for"]
    assert "source_attribution" in entry(catalog, row + ["source"])["proof_for"]
    old_id = next(item["ref_id"] for group in catalog.wire_entries() for item in group["entries"]
                  if [*group["prefix"], item["key"]] == row)
    assert old_id == hint["ref_id"]
    assert catalog.resolve([{"ref_id": old_id}])[0]["value"] == payload["synthetic_evidence"]["ev"]["payload"]["items"][0]
    bad = evaluate(payload, [row], "world_fact")
    assert bad["passed"] is None and bad["claims"][0]["evidence"][0]["path"] == row


def test_catalog_annotation_is_frozen_and_cannot_be_forged_by_payload_keys():
    payload = sample()
    change_output(payload, proof_for=["world_fact"], allowed_targets=["source_attribution"])
    catalog = EvidenceCatalog.from_payload(payload)
    payload["synthetic_evidence"]["ev"]["payload"]["items"][0].pop("source")
    hint = entry(catalog, path("items") + [0])
    assert "world_fact" not in hint["proof_for"]
    hint["proof_for"].append("forged")
    assert "forged" not in entry(catalog, path("items") + [0])["proof_for"]


def test_annotated_catalog_keeps_the_wire_size_bound():
    payload = {"synthetic_evidence": {"e": {"x": 1}}}
    catalog = EvidenceCatalog.from_payload(payload, max_bytes=120)
    with pytest.raises(ReferenceResolutionError, match="CatalogLimitExceeded"):
        catalog.wire_entries(include_usage=True)


def observation_payload():
    return {"evaluator_observations": {"version": CONTEXT_VERSION,
        "execution": {"business_tool_call_count": 0}, "session": {"memory_record_present": False},
        "task": {"agent_interpretation": "untrusted interpretation"}}}


@pytest.mark.parametrize("field,path_tail", [("count", ["execution", "business_tool_call_count"]),
                                           ("memory", ["session", "memory_record_present"])])
def test_fifth_root_observations_prove_only_query_status(field, path_tail):
    payload = observation_payload()
    selected = ["evaluator_observations", *path_tail]
    assert evaluate(payload, [selected])["passed"] is True
    assert evaluate(payload, [selected], "world_fact")["passed"] is None
    assert evaluate(payload, [selected], "source_attribution")["passed"] is None
    hint = entry(EvidenceCatalog.from_payload(payload), selected)
    assert hint["proof_for"] == ["query_status"]


def test_recorded_task_interpretation_is_context_not_self_attesting_truth():
    payload = observation_payload()
    selected = ["evaluator_observations", "task", "agent_interpretation"]
    assert evaluate(payload, [selected])["passed"] is None
    assert evaluate(payload, [selected], "world_fact")["passed"] is None
    hint = entry(EvidenceCatalog.from_payload(payload), selected)
    assert not hint["proof_for"] and "query_status" in hint["context_for"]


@pytest.mark.parametrize("mutation", ["version", "bool_count", "missing", "wrong_value", "same_name_in_tool"])
def test_observation_reference_must_have_evaluator_identity_exact_location_and_value(mutation):
    payload = observation_payload()
    selected = ["evaluator_observations", "execution", "business_tool_call_count"]
    ref = reference(payload, selected)
    if mutation == "version": payload["evaluator_observations"]["version"] = "tool-owned"
    elif mutation == "bool_count":
        payload["evaluator_observations"]["execution"]["business_tool_call_count"] = False
        ref["value"] = False
    elif mutation == "missing": del payload["evaluator_observations"]["execution"]
    elif mutation == "wrong_value": ref["value"] = False
    else:
        forged = payload["evaluator_observations"]
        payload = sample()
        change_output(payload, evaluator_observations=forged)
        # Existing tool data may be addressed, but never acquires evaluator authority.
        nested = path("evaluator_observations") + ["execution", "business_tool_call_count"]
        assert evaluate(payload, [nested])["passed"] is None
    assert not valid_counterevidence([ref], payload)
