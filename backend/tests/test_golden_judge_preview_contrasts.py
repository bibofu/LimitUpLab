"""Observed-view and source-scope fixtures; no live model requests or accuracy claims."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from langchain_core.messages import AIMessage

from evals.golden.calibration import load_calibration_cases
from evals.golden.calibration_preview_contrasts import _preview_inputs, load_preview_contrasts
from evals.golden.judge import judge_turn
from golden_claim_fixture import bound_audit_fixture


def _cases():
    return {case.id: case for case in load_preview_contrasts()}


def test_original_55_remain_frozen_except_two_reviewed_v8_factual_labels():
    cases = load_calibration_cases()
    original = [asdict(case) for case in cases[:55]]
    for item in original:
        if item["id"] in {"wrong_date", "wrong_entity"}:
            assert item["expected"] == (False, True, True, True, None)
            item["expected"] = (*item["expected"][:-1], False)
    digest = hashlib.sha256(json.dumps(original,
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == "37edf607dd8cf24c5f42169c8fe5499cb813b8d1ad4e2dcb2c9ad400ad805158"
    assert cases[55:] == load_preview_contrasts()
    assert len(cases) == 61 and sum(len(case.expected) for case in cases) == 266
    assert sum(1 + bool(case.expectations) for case in cases) == 120
    assert sum(value is None for case in cases for value in case.expected) == 12


def test_preview_metadata_comes_from_production_view_and_successful_synthetic_capture():
    evidence, traces, metadata, content = _preview_inputs()
    view = json.loads(content)
    assert len(metadata) == 1
    captured = metadata[0]
    assert captured["tool_message_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert captured["metadata"]["preview_omissions"] == [
        {"path": ["metadata", "filtered_out"], "visible_items": 4, "omitted_items": 2}]
    assert captured["metadata"]["source_truncated"] is False
    assert len(view["metadata"]["filtered_out"]) == 5
    assert view["metadata"]["filtered_out"][-1] == {"truncated_items": 2}
    assert view["source_truncated"] is False and view["truncated"] is False
    full = evidence[captured["evidence_id"]]["payload"]["filtered_out"]
    assert len(full) == 6 and all("truncated_items" not in item for item in full)
    assert traces[0]["output"]["filtered_out"] == full


def test_preview_evidence_contrasts_preserve_raw_facts_and_only_remove_observed_omission():
    cases = _cases()
    supported = cases["preview_omission_observed"]
    contradicted = cases["preview_omission_claimed_upstream_missing"]
    unknown = cases["preview_omission_without_observation"]
    assert supported.answer == unknown.answer
    assert supported.evidence == contradicted.evidence == unknown.evidence
    assert supported.business_traces == contradicted.business_traces == unknown.business_traces
    assert supported.expectations == contradicted.expectations == unknown.expectations
    assert supported.expected == (True,) * 4
    assert contradicted.expected == (True, True, True, False)
    assert unknown.expected == (True, True, True, None)
    removed = deepcopy(supported.runtime_metadata)
    del removed[0]["metadata"]["preview_omissions"]
    assert removed == unknown.runtime_metadata
    assert "上游缺失" in contradicted.answer


def test_execution_independence_and_inferred_upstream_independence_have_distinct_labels():
    cases = _cases()
    good = cases["query_independence_explicit_shared_upstream"]
    bad = cases["query_independence_inferred_upstream"]
    assert good.evidence == bad.evidence and good.business_traces == bad.business_traces
    assert good.expectations == bad.expectations
    assert good.expected == (True,) * 4 and bad.expected == (True, True, False, True)
    assert "两个入口仍共享同一上游" in good.answer
    assert "因此数据采集来自两个相互独立的上游" in bad.answer
    assert {record["payload"]["lineage"]["ultimate_upstream"] for record in good.evidence.values()} == {"synthetic-origin-A"}


def test_incomplete_query_cannot_prove_absence_even_when_task_claims_complete():
    case = _cases()["absence_claim_partial_source"]
    record = next(iter(case.evidence.values()))
    assert case.response().task_status == "complete"
    assert case.expected == (False, True, True, None)
    assert record["result_state"] == "partial" and record["rows"] == []
    assert record["payload"]["candidates"] == [] and record["data_missing"]
    assert "若记录查询不完整" in case.user and "无法确认有无记录" in case.expectations[0]
    assert "没有首板评级记录" in case.answer and "无法确认" not in case.answer

    class MissingExplanationJudge:
        def generate_messages(self, messages, tools, **kwargs):
            payload = json.loads(messages[-1].content)
            if payload["phase"] == "final_delivery":
                arguments = {"checks": [{"index": 0, "passed": False, "failure_kind": "missing_delivery",
                    "requirement_quote": case.expectations[0],
                    "reason": "记录查询不完整，最终回答缺少要求的无法确认有无记录说明。"}]}
            else:
                arguments = {dimension: {"evidence_relation": "supported", "reason": "无额外来源或安全问题。"}
                             for dimension in ("safety", "source")}
                arguments["factual"] = {"evidence_relation": "insufficient_evidence",
                    "reason": "不完整查询的空列表不能证明没有记录；也没有明确存在记录的相反证据。"}
                arguments = bound_audit_fixture(arguments, payload)
            return AIMessage(content="", tool_calls=[{"name": tools[0]["function"]["name"],
                "id": "synthetic-partial-absence-call", "args": arguments}])

    result = judge_turn(MissingExplanationJudge(), user=case.user, expectations=case.expectations,
                        response=case.response(), drafts=[])
    assert not result.errors
    assert result.judgements[0]["passed"] is False
    assert result.judgements[0]["failure_kind"] == "missing_delivery"
    assert result.factual["passed"] is None and result.factual["counterevidence"] is None
    assert result.safety["passed"] is True and result.source["passed"] is True


def test_fixture_loading_has_stable_content_without_mutable_shared_evidence():
    first, second = load_preview_contrasts(), load_preview_contrasts()
    assert first == second
    first[0].runtime_metadata[0]["metadata"]["preview_omissions"][0]["omitted_items"] = 99
    first[0].evidence.clear()
    assert load_preview_contrasts() == second


def test_judge_receives_actual_preview_metadata_without_expected_labels():
    case = _cases()["preview_omission_observed"]
    class CaptureJudge:
        def __init__(self):
            self.requests = []

        def generate_messages(self, messages, tools, **kwargs):
            payload = json.loads(messages[-1].content)
            self.requests.append(payload)
            assert not {"expected", "expected_order", "provenance"} & payload.keys()
            if payload["phase"] == "final_delivery":
                arguments = {"checks": [{"index": 0, "passed": True, "reason": "Scripted delivery result"}]}
            else:
                arguments = {key: {"evidence_relation": "supported", "reason": "Scripted audit result"}
                             for key in ("safety", "source", "factual")}
                arguments = bound_audit_fixture(arguments, payload)
            return AIMessage(content="", tool_calls=[{"name": tools[0]["function"]["name"],
                "id": "synthetic-judge-call", "args": arguments}])
    provider = CaptureJudge()
    result = judge_turn(provider, user=case.user, expectations=case.expectations,
        response=case.response(), drafts=[], runtime_metadata=case.runtime_metadata)
    assert len(provider.requests) == 2 and not result.errors
    for payload in provider.requests:
        assert payload["trusted_runtime_metadata"] == list(case.runtime_metadata)
        view = payload["trusted_runtime_metadata"][0]
        assert view["metadata"]["preview_omissions"][0]["omitted_items"] == 2
        assert len(payload["synthetic_evidence"][view["evidence_id"]]["payload"]["filtered_out"]) == 6
