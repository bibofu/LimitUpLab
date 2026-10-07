"""Fixture boundaries for judge criteria; these tests do not measure model accuracy."""

from dataclasses import asdict
import hashlib
import json

from evals.golden.calibration import load_calibration_cases
from evals.golden.calibration_criteria_contrasts import load_criteria_contrasts


def _cases():
    return {case.id: case for case in load_criteria_contrasts()}


def test_original_37_remain_frozen_except_two_reviewed_v8_factual_labels():
    # Restore only the two reviewed labels before checking the frozen v4 hash.
    original = [asdict(case) for case in load_calibration_cases()[:37]]
    for item in original:
        if item["id"] in {"wrong_date", "wrong_entity"}:
            assert item["expected"] == (False, True, True, True, None)
            item["expected"] = (*item["expected"][:-1], False)
    digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == "03d50a9b01c4de229a9e3e125bdf66c36474b7d96fa2b174f778d394ce9541d2"
    assert len(load_criteria_contrasts()) == 12
    assert load_calibration_cases()[37:49] == load_criteria_contrasts()


def test_failure_paraphrases_have_identical_evidence_without_identifier_requirements():
    cases = _cases()
    variants = [cases[key] for key in (
        "failure_without_tool_identifier", "failure_equivalent_wording", "failure_cause_not_claimed")]
    assert len({case.answer for case in variants}) == 3
    assert all(case.expectations == variants[0].expectations for case in variants)
    assert all(case.business_traces == variants[0].business_traces for case in variants)
    for case in variants:
        assert "limit_up_events" not in case.answer and "RuntimeError" not in case.answer
        assert case.expected == (True,) * 4
        assert not case.drafts and not case.evidence


def test_explicit_tool_name_requirement_is_not_erased_by_scope_rule():
    cases = _cases()
    optional = cases["failure_without_tool_identifier"]
    required = cases["failure_required_identifier_missing"]
    assert optional.answer == required.answer
    assert optional.business_traces == required.business_traces
    assert required.expectations[0] == optional.expectations[0] + "同时写明执行失败的工具名称。"
    assert required.expected == (False, True, True, True)


def test_alternative_requirement_accepts_each_supported_branch_without_inventing_the_other():
    cases = _cases()
    stale, failed = (cases[key] for key in ("failure_or_date_only", "failure_or_query_only"))
    assert stale.expectations == failed.expectations
    assert stale.expected == failed.expected == (True,) * 4
    assert "查询失败" not in stale.answer and "日期不匹配" in stale.answer
    assert "日期不匹配" not in failed.answer and "查询失败" in failed.answer
    assert stale.business_traces[0]["status"] == "success"
    stale_output = stale.business_traces[0]["output"]
    assert stale_output["requested_date"] != stale_output["data_as_of"]
    assert failed.business_traces[0]["status"] == "error"
    assert "data_as_of" not in failed.business_traces[0]["output"]


def test_generic_noncompletion_is_missing_delivery_without_false_factual_assertion():
    cases = _cases()
    explicit, generic = (cases[key] for key in (
        "failure_without_tool_identifier", "failure_generic_without_explanation"))
    assert explicit.business_traces == generic.business_traces
    assert explicit.expectations == generic.expectations
    assert generic.expected == (False, True, True, True)
    assert "查询失败" not in generic.answer and not generic.drafts


def test_unknown_and_contradicted_cause_keep_same_claim_and_delivery_scope():
    cases = _cases()
    unknown, contradicted = (cases[key] for key in (
        "failure_specific_cause_unknown", "failure_specific_cause_contradicted"))
    assert unknown.answer == contradicted.answer
    assert "HTTP 503" in unknown.answer and "超时" not in unknown.answer
    assert unknown.expectations == contradicted.expectations
    assert unknown.expected == (True, True, True, None)
    assert contradicted.expected == (True, True, True, False)
    unknown_output, contradicted_output = (case.business_traces[0]["output"] for case in (unknown, contradicted))
    assert "network_request_sent" not in unknown_output and "http_status" not in unknown_output
    assert contradicted_output["network_request_sent"] is False
    assert contradicted_output["failure_stage"] == "local_parameter_validation"


def test_query_independence_does_not_imply_source_independence():
    cases = _cases()
    queries, shared, unknown = (cases[key] for key in (
        "independent_queries_shared_origin", "independent_sources_shared_origin",
        "independent_sources_unknown_origin"))
    assert queries.evidence == shared.evidence and queries.business_traces == shared.business_traces
    assert queries.expectations == shared.expectations == unknown.expectations
    assert shared.answer == unknown.answer == queries.answer + "这两份结果来自相互独立的上游来源。"
    assert queries.expected == (True,) * 4
    assert shared.expected == (True, True, False, True)
    assert unknown.expected == (True, True, None, True)
    assert len({trace["output"]["lineage"]["ultimate_upstream"] for trace in shared.business_traces}) == 1
    assert all("lineage" not in trace["output"] for trace in unknown.business_traces)
