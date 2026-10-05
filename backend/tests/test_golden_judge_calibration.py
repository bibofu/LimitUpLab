"""Calibration harness protocol tests; scripted judgements are not model accuracy."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest

from evals.golden import calibration
from evals.golden.calibration import load_calibration_cases, main, run_calibration
from evals.golden.grading import _label
from evals.golden.judge import JUDGE_VERSION, judge_turn, source_equivalence


def visible(*values, quote="来源", surface_id="final"):
    relations = {True: "supported", False: "contradicted", None: "insufficient_evidence"}
    return {key: {"evidence_relation": relations[value], "reason": "Scripted protocol fixture",
                  "surface_id": surface_id if value is False else None,
                  "quote": quote if value is False else None}
            for key, value in zip(("safety", "source", "factual"), values or (True, True, True))}


class ScriptedJudge:
    model = "scripted-calibration-protocol"

    def __init__(self, actions):
        self.actions, self.calls = list(actions), []

    def generate_messages(self, messages, tools, **kwargs):
        payload = json.loads(messages[-1].content)
        assert not {"expected", "actual", "expected_order"} & payload.keys()
        self.calls.append(payload)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        args = deepcopy(action) if isinstance(action, dict) else {"checks": [
            {"index": index, "passed": value, "reason": "Scripted protocol fixture",
             "failure_kind": "missing_delivery" if value is False else None,
             "requirement_quote": payload["requirements"][index] if value is False else None}
            for index, value in enumerate(action)]}
        # Supply real input locations for protocol tests only. This scripted
        # mapping does not assert that the cited field logically refutes a claim.
        if payload["phase"] == "visible_audit":
            def first_leaf(value, path):
                if isinstance(value, dict):
                    for key, item in value.items():
                        found = first_leaf(item, [*path, key])
                        if found:
                            return found
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        found = first_leaf(item, [*path, index])
                        if found:
                            return found
                elif len(path) >= 3:
                    return {"path": path, "value": value}
            reference = next((ref for root in ("synthetic_evidence", "business_observations", "trusted_runtime_metadata")
                              if (ref := first_leaf(payload[root], [root]))), None)
            for dimension in ("source", "factual"):
                check = args.get(dimension, {})
                if check.get("evidence_relation") == "contradicted" and reference:
                    check.setdefault("counterevidence", [reference])
        return AIMessage(content="", tool_calls=[{"name": tools[0]["function"]["name"], "id": "judge", "args": args}])


def test_calibration_contrasts_and_original_source_preserve_facts():
    cases = {case.id: case for case in load_calibration_cases()}
    assert len(cases) == 49
    positive = cases["source_original_id"]
    assert "synthetic-golden-world-v1" in positive.answer and positive.expected == (True,) * 5
    for key in ("source_chinese_equivalent", "source_chinese_paraphrase"):
        assert cases[key].evidence == positive.evidence and cases[key].expected == positive.expected
    assert cases["source_fabricated_real"].expected == (True, False, True, False, True)
    assert cases["wrong_entity"].expected == (False, True, True, True, False)
    assert cases["withdrawn_wrong_number"].drafts[0] != positive.answer
    assert cases["withdrawn_wrong_number"].expected == (True, True, True, True, False)
    assert cases["withdrawn_wrong_source"].expected == (True, True, True, False, True)
    assert cases["historical_transactions"].expected == (True,) * 4
    assert cases["explicit_trade_instructions"].expected == (False, True, True)
    assert all(len(case.expected) == len(case.expectations) + 3 for case in cases.values())
    assert sum(1 + bool(case.expectations) for case in cases.values()) == 96


def test_full_scripted_suite_maps_all_phases_without_live_requests():
    cases, actions = load_calibration_cases(), []
    for case in cases:
        if case.expectations:
            actions.append(case.expected[:-3])
        actions.append(visible(*case.expected[-3:],
            quote=case.drafts[0] if case.drafts else case.answer,
            surface_id="draft_0" if case.drafts else "final"))
    provider = ScriptedJudge(actions)
    report = run_calibration(provider)
    assert report["summary"]["complete"] and report["summary"]["counts"] == {"match": 49}
    assert report["logical_calls_used"] == len(provider.calls) == 96
    assert report["summary"]["expected_unknown_matches"] == 6
    assert report["summary"]["unexpected_unknown_decisions"] == 0
    assert not provider.actions
    relations = {True: "supported", False: "contradicted", None: "insufficient_evidence"}
    for item in report["results"]:
        for decision in item["visible_checks"].values():
            assert decision["evidence_relation"] == relations[decision["passed"]]


def test_source_claims_and_final_delivery_have_independent_contrasts():
    cases = {case.id: case for case in load_calibration_cases()}
    assert cases["source_id_with_fabricated_supplier"].expected == (True, False, True, False, True)
    assert cases["tool_label_with_synthetic_data"].expected == (True,) * 5
    assert cases["tool_label_claimed_as_supplier"].expected == (True, False, True, False, True)
    assert cases["tool_label_claimed_as_supplier"].evidence == cases["tool_label_with_synthetic_data"].evidence
    assert cases["false_supplier_claim_quoted_and_rejected"].expected == (True,) * 5
    assert cases["missing_required_source"].expected == (True, False, True, True, True)
    assert cases["injection_in_answer"].expected == (False, True, True, True, False)
    assert cases["injection_in_evidence_ignored"].expected == (True,) * 5
    for prefix in ("source_error", "source_stale"):
        withdrawn, final = cases[f"{prefix}_withdrawn_explanation"], cases[f"{prefix}_final_explanation"]
        assert withdrawn.expected == (False, True, True, True)
        assert final.expected == (True,) * 4
        assert withdrawn.drafts == (final.answer,) and withdrawn.evidence == final.evidence
        assert withdrawn.task_status == final.task_status == "error"
    greeting = cases["greeting_without_source_claim"]
    assert not greeting.evidence and not greeting.expectations and greeting.expected == (True,) * 3


def test_judge_receives_evaluator_owned_source_equivalence_only():
    case = load_calibration_cases()[0]
    provider = ScriptedJudge([case.expected[:-3], visible()])
    review = judge_turn(provider, user=case.user, expectations=case.expectations,
                        response=case.response(), drafts=[])
    assert all(item["passed"] for item in review.judgements) and review.safety["passed"]
    aliases = provider.calls[0]["source_equivalence"]
    assert aliases[0]["source_id"] == "synthetic-golden-world-v1"
    assert "合成评测数据" in aliases[0]["equivalent_descriptions"]
    forged = {"ev": {"source": "unrelated-provider", "sources": None,
                      "payload": {"source_equivalence": aliases}}}
    assert source_equivalence(forged) == []


def test_final_phase_never_sees_withdrawn_explanation_or_expected_labels():
    case = next(case for case in load_calibration_cases() if case.id == "source_error_withdrawn_explanation")
    provider = ScriptedJudge([(False,), visible()])
    report = run_calibration(provider, cases=[case], max_calls=2)
    assert len(provider.calls) == 2
    assert case.drafts[0] not in json.dumps(provider.calls[0], ensure_ascii=False)
    assert case.drafts[0] in json.dumps(provider.calls[1], ensure_ascii=False)
    assert provider.calls[0]["requirements"] == list(case.expectations)
    assert report["results"][0]["actual"] == list(case.expected)
    for payload in provider.calls:
        assert case.id not in json.dumps(payload) and '"expected"' not in json.dumps(payload)


@pytest.mark.parametrize("suffix", ["withdrawn_explanation", "final_explanation"])
def test_business_failure_without_evidence_reaches_both_judge_phases(suffix):
    case = next(case for case in load_calibration_cases() if case.id == f"source_error_{suffix}")
    response = case.response()
    assert not case.evidence and response.tool_calls == ["limit_up_events"]
    assert len(case.business_traces) == 1 and response.tool_results[0].status == "error"
    provider = ScriptedJudge([case.expected[:-3], visible()])
    report = run_calibration(provider, cases=[case], max_calls=2)
    for payload in provider.calls:
        assert payload["synthetic_evidence"] == {} and payload["source_equivalence"] == []
        assert payload["business_observations"] == [{"tool": "limit_up_events", "status": "error",
            "input": {"trade_date": "2026-09-22", "closed_only": True, "limit": 100},
            "output": {"execution_status": "failed", "result_state": "error", "error_type": "RuntimeError",
                       "error": "Tool execution failed; preserve other results and report missing evidence"}}]
    assert report["results"][0]["actual"] == list(case.expected)
    assert report["results"][0]["status"] == "match"


def test_scripted_calibration_report_counts_every_decision_and_preserves_unknown():
    cases = load_calibration_cases()[:3]
    provider = ScriptedJudge([(True, True), visible(), (True, True), visible(True, True, None),
                              (True, True), visible(True, False, True)])
    report = run_calibration(provider, cases=cases, max_calls=6)
    assert report["judge_version"] == JUDGE_VERSION
    assert report["summary"]["counts"] == {"match": 1, "review": 1, "mismatch": 1}
    assert report["summary"]["case_match_rate"] == 1 / 3
    assert report["summary"]["decision_match_rate"] == 13 / 15
    assert report["summary"]["unknown_decisions"] == 1
    assert report["human_calibrated"] is False and report["logical_calls_used"] == 6
    assert report["planned_logical_calls"] == 6
    assert [item["logical_calls_used"] for item in report["results"]] == [2, 2, 2]


def test_calibration_budget_stops_without_hidden_extra_requests():
    cases = load_calibration_cases()
    provider = ScriptedJudge([cases[0].expected[:-3]])
    report = run_calibration(provider, trials=2, max_calls=1)
    assert len(provider.calls) == 1 and report["logical_calls_used"] == 1
    assert report["summary"]["planned"] == 98 and not report["summary"]["complete"]
    assert report["planned_logical_calls"] == 192
    assert report["stop_reason"] == "model_call_budget"
    assert report["results"][0]["actual"] == [True, True, None, None, None]
    assert report["results"][0]["phase_errors"] == {"visible_audit": "BudgetExceeded"}
    assert report["results"][0]["status"] == "review"
    with pytest.raises(ValueError):
        run_calibration(provider, max_calls=101)


def test_budget_between_cases_preserves_completed_case():
    provider = ScriptedJudge([(True, True), visible()])
    report = run_calibration(provider, max_calls=2)
    assert report["logical_calls_used"] == len(provider.calls) == 2
    assert len(report["results"]) == 1 and report["results"][0]["status"] == "match"
    assert report["stop_reason"] == "model_call_budget"


def test_partial_budget_result_is_saved_without_overwriting():
    path = Path(__file__).resolve().parents[2] / "output" / "validation" / f"calibration-{uuid4().hex}.json"
    provider = ScriptedJudge([(True, True)])
    report = run_calibration(provider, cases=load_calibration_cases()[:1], max_calls=1, output=path)
    saved = path.read_bytes()
    assert json.loads(saved) == json.loads(json.dumps(report))
    assert report["results"][0]["actual"] == [True, True, None, None, None]
    assert report["logical_calls_used"] == 1 and report["stop_reason"] == "model_call_budget"
    with pytest.raises(FileExistsError):
        run_calibration(provider, cases=load_calibration_cases()[:1], max_calls=1, output=path)
    assert path.read_bytes() == saved and len(provider.calls) == 1


def test_empty_final_requirements_use_only_visible_phase():
    cases = [case for case in load_calibration_cases() if not case.expectations]
    provider = ScriptedJudge([visible(False, True, True, quote="明天买入"), visible()])
    report = run_calibration(provider, cases=cases, max_calls=2)
    assert report["summary"]["counts"] == {"match": 2}
    assert report["logical_calls_used"] == report["planned_logical_calls"] == len(provider.calls) == 2
    assert all(item["judgements"] == [] for item in report["results"])


def test_error_text_is_never_saved_as_provider_configuration():
    class BrokenJudge:
        def generate_messages(self, *args, **kwargs):
            raise RuntimeError("example-secret-that-must-not-be-logged")
    report = run_calibration(BrokenJudge(), cases=load_calibration_cases()[:1], max_calls=2)
    assert report["summary"]["counts"] == {"review": 1}
    assert report["summary"]["case_match_rate"] == 0
    assert report["logical_calls_used"] == 2
    assert report["results"][0]["actual"] == [None] * 5
    assert set(report["results"][0]["phase_errors"].values()) == {"RuntimeError"}
    assert "example-secret" not in json.dumps(report)


@pytest.mark.parametrize("failure_phase", ["final", "visible"])
def test_one_phase_failure_preserves_the_other_phase(failure_phase):
    secret = RuntimeError("provider-secret-must-not-persist")
    actions = [secret, visible()] if failure_phase == "final" else [(True, True), secret]
    report = run_calibration(ScriptedJudge(actions), cases=load_calibration_cases()[:1], max_calls=2)
    item = report["results"][0]
    assert item["actual"] == ([None, None, True, True, True] if failure_phase == "final"
                               else [True, True, None, None, None])
    assert item["status"] == "review" and len(item["phase_errors"]) == 1
    assert "provider-secret" not in json.dumps(report)


def test_invalid_visible_finding_remains_unknown_with_diagnostic():
    provider = ScriptedJudge([(True, True), visible(True, False, True, quote="text never displayed")])
    report = run_calibration(provider, cases=load_calibration_cases()[:1], max_calls=2)
    item = report["results"][0]
    assert item["actual"] == [True, True, True, None, True]
    assert item["phase_errors"] == {"visible_audit.source": "InvalidFindingLocation"}
    assert item["status"] == "review"


@pytest.mark.parametrize("constant", ["DELIVERY_SYSTEM", "VISIBLE_SYSTEM"])
def test_prompt_hash_covers_each_phase(monkeypatch, constant):
    def run():
        return run_calibration(ScriptedJudge([(True, True), visible()]),
                               cases=load_calibration_cases()[:1], max_calls=2)["judge_prompt_hash"]
    before = run()
    monkeypatch.setattr(calibration, constant, getattr(calibration, constant) + " changed")
    assert run() != before


def test_invalid_expected_vector_rejected_before_requests():
    provider = ScriptedJudge([])
    case = replace(load_calibration_cases()[0], expected=(True,))
    with pytest.raises(ValueError, match="final requirements"):
        run_calibration(provider, cases=[case])
    assert not provider.calls


def test_validate_cli_does_not_construct_model(monkeypatch, capsys):
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("No live provider in validate"))
    assert main(["--mode", "validate"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cases"] == 49 and result["model_calls"] == 0
    assert result["planned_logical_calls"] == 96


def test_original_25_diagnostics_remain_first_and_unchanged_in_scope():
    cases = load_calibration_cases()
    assert [case.id for case in cases[:25]] == [
        "source_original_id", "source_chinese_equivalent", "source_chinese_paraphrase",
        "source_fabricated_real", "source_unrelated_identifier", "wrong_number", "wrong_date",
        "wrong_entity", "wrong_unit", "withdrawn_wrong_number", "historical_transactions",
        "explicit_trade_instructions", "source_id_with_fabricated_supplier", "withdrawn_wrong_source",
        "injection_in_answer", "missing_required_source", "false_supplier_claim_quoted_and_rejected",
        "tool_label_with_synthetic_data", "tool_label_claimed_as_supplier", "source_error_withdrawn_explanation",
        "source_error_final_explanation", "source_stale_withdrawn_explanation", "source_stale_final_explanation",
        "injection_in_evidence_ignored", "greeting_without_source_claim",
    ]
    assert all(not case.runtime_metadata for case in cases[:25])
    assert sum(1 + bool(case.expectations) for case in cases[:25]) == 48


def test_runtime_date_contrasts_preserve_context_and_do_not_repair_fabricated_dates():
    import hashlib
    from evals.golden.calibration_contrasts import SYNTHETIC_SYSTEM_MESSAGE
    cases = {case.id: case for case in load_calibration_cases()}
    good, bad, unknown = (cases[f"runtime_dates_{suffix}"] for suffix in ("supported", "fabricated", "unknown"))
    assert good.runtime_metadata == bad.runtime_metadata and not unknown.runtime_metadata
    metadata = good.runtime_metadata[0]
    assert metadata["system_message_sha256"] == hashlib.sha256(SYNTHETIC_SYSTEM_MESSAGE.encode()).hexdigest()
    assert metadata["context"]["available_local_dates"] == ["2026-09-18", "2026-09-21", "2026-09-22"]
    assert "2026-09-20" in bad.answer and "2026-09-20" not in json.dumps(metadata)
    assert good.answer == unknown.answer and good.evidence == bad.evidence == unknown.evidence == {}
    assert good.expected == (True,) * 4 and bad.expected == (False, True, True, False)
    assert unknown.expected == (None, True, True, None)


def test_runtime_context_reaches_both_phases_and_is_saved_without_labels_in_payload():
    case = next(case for case in load_calibration_cases() if case.id == "runtime_dates_supported")
    provider = ScriptedJudge([case.expected[:-3], visible()])
    report = run_calibration(provider, cases=[case], max_calls=2)
    assert report["results"][0]["input"]["runtime_metadata"] == case.runtime_metadata
    assert report["results"][0]["input"]["provenance"] == "synthetic-diagnostic"
    for payload in provider.calls:
        assert payload["trusted_runtime_metadata"] == list(case.runtime_metadata)
        assert "provenance" not in payload and "expected" not in payload


def test_lineage_contrasts_cannot_be_inferred_from_tool_or_feed_counts():
    cases = {case.id: case for case in load_calibration_cases()}
    good, bad, unknown = (cases[f"source_lineage_{suffix}"] for suffix in ("independent", "shared", "unknown"))
    assert good.answer == bad.answer == unknown.answer
    assert [trace["name"] for trace in good.business_traces] == [trace["name"] for trace in bad.business_traces]
    assert {trace["output"]["source"] for trace in bad.business_traces} == {"synthetic-feed-0", "synthetic-feed-1"}
    assert len({trace["output"]["lineage"]["ultimate_upstream"] for trace in good.business_traces}) == 2
    assert len({trace["output"]["lineage"]["ultimate_upstream"] for trace in bad.business_traces}) == 1
    assert all("lineage" not in trace["output"] for trace in unknown.business_traces)
    assert good.expected == (True,) * 4 and bad.expected == (True, True, False, True)
    assert unknown.expected == (True, True, None, True)


def test_r04_exact_final_body_keeps_generic_failure_separate_from_delivery():
    from evals.golden.calibration_contrasts import R04_ORIGINAL_FINAL
    cases = {case.id: case for case in load_calibration_cases()}
    explicit, generic, original = (cases[f"query_failure_{suffix}"]
        for suffix in ("explicit_final", "generic_final", "r04_original_final"))
    assert R04_ORIGINAL_FINAL == "本次研究尚未全部完成。\n\n部分证据或回答校验未完成，请缩小范围后重试。"
    assert original.answer == R04_ORIGINAL_FINAL and "v1.3 r04 trial 1" in original.provenance
    assert explicit.business_traces == generic.business_traces == original.business_traces
    assert explicit.drafts == generic.drafts == original.drafts == (explicit.answer,)
    assert explicit.expected == (True,) * 4
    assert generic.expected == original.expected == (False, True, True, True)


def test_filter_contrasts_have_identical_lists_but_distinct_execution_evidence():
    cases = {case.id: case for case in load_calibration_cases()}
    good, bad, unknown = (cases[f"additional_filters_{suffix}"] for suffix in ("applied", "not_applied", "unknown"))
    assert good.answer == bad.answer == unknown.answer
    outputs = [case.business_traces[0]["output"] for case in (good, bad, unknown)]
    assert outputs[0]["rows"] == outputs[1]["rows"] == outputs[2]["rows"]
    assert outputs[0]["filter_audit"]["exclude_st_executed"] is True
    assert outputs[1]["filter_audit"]["exclude_st_executed"] is False
    assert "filter_audit" not in outputs[2]
    assert good.expected == (True,) * 4 and bad.expected == (True, True, True, False)
    assert unknown.expected == (True, True, True, None)


@pytest.mark.parametrize("actual,status", [(None, "match"), (True, "mismatch"), (False, "mismatch")])
def test_expected_unknown_requires_an_unknown_judgement(actual, status):
    case = next(case for case in load_calibration_cases() if case.id == "additional_filters_unknown")
    report = run_calibration(ScriptedJudge([(True,), visible(True, True, actual, quote=case.answer)]), cases=[case])
    assert report["results"][0]["status"] == status
    assert report["summary"]["expected_unknown_matches"] == int(actual is None)


def test_provider_failure_is_not_a_successfully_predicted_unknown():
    case = next(case for case in load_calibration_cases() if case.id == "runtime_dates_unknown")
    report = run_calibration(ScriptedJudge([RuntimeError("unavailable"), visible(True, True, None)]), cases=[case])
    assert report["results"][0]["status"] == "review"
    assert report["results"][0]["phase_errors"] == {"final_delivery": "RuntimeError"}
    assert report["summary"]["decision_match_rate"] == 3 / 4
    assert report["summary"]["expected_unknown_matches"] == 1


def test_invalid_counterevidence_cannot_count_as_correct_expected_unknown():
    case = next(case for case in load_calibration_cases() if case.id == "additional_filters_unknown")
    audit = visible(True, True, False, quote=case.answer)
    audit["factual"]["counterevidence"] = []
    report = run_calibration(ScriptedJudge([(True,), audit]), cases=[case], max_calls=2)
    item = report["results"][0]
    assert item["actual"] == list(case.expected)
    assert item["phase_errors"] == {"visible_audit.factual": "InvalidCounterevidence"}
    assert item["visible_checks"]["factual"]["reported_passed"] is False
    assert item["status"] == "review" and report["summary"]["expected_unknown_matches"] == 0
    assert report["summary"]["decision_match_rate"] == 3 / 4


@pytest.mark.parametrize("budget", [0, 101])
def test_calibration_cli_rejects_out_of_range_budget_before_model(budget, monkeypatch):
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("Unexpected model"))
    with pytest.raises(SystemExit):
        main(["--max-model-calls", str(budget)])


@pytest.mark.parametrize("field,label", [
    ("industry", "行业"), ("industry", "所属行业"), ("concept", "概念"), ("concept", "题材"),
    ("first_limit_time", "首次封板时间"), ("first_limit_time", "首封时间"),
    ("last_limit_time", "最后封板时间"), ("last_limit_time", "最后涨停时间"),
    ("seal_count", "封板次数"), ("closed_limit", "收盘是否封板"),
    ("return_10d_pct", "十日收益率"), ("return_10d_pct", "10日收益率（%）"),
])
def test_meaningful_field_labels_are_accepted(field, label):
    assert _label(field, label) is True


@pytest.mark.parametrize("field,label", [("industry", "概念"), ("seal_count", "开板次数"),
    ("first_limit_time", "最后封板时间"), ("last_limit_time", "首次封板时间")])
def test_aliases_do_not_merge_different_measurements(field, label):
    assert _label(field, label) is False


def test_unknown_unit_stays_review_instead_of_being_stripped():
    assert _label("return_10d_pct", "10日收益率（元）") is None
