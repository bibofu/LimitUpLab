"""Scope and scoring protocol regressions, without claims of model accuracy."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

import pytest

from app.models import AgentToolTrace
from evals.golden.calibration import load_calibration_cases
from evals.golden.calibration_scope_contrasts import load_scope_contrasts
from evals.golden.cases import load_cases
from evals.golden.contracts import verdict
from evals.golden.grading import grade_turn


def _cases():
    return {case.id: case for case in load_scope_contrasts()}


def _recorded_response(case, *, task_status=None):
    response = case.response()
    response.task_status = task_status or case.task_status
    finish = {"name": "finish", "args": {"status": response.task_status, "answer": case.answer,
        "evidence_ids": list(case.evidence), "missing": []}}
    response.tool_results.insert(-1, AgentToolTrace(name="react_decision", summary="Synthetic finish",
        output={"tool_calls": [finish]}))
    return response


def _decisions(values):
    return [{"index": index, "passed": value, "reason": "Scripted diagnostic label; not model accuracy"}
            for index, value in enumerate(values)]


def test_original_49_diagnostics_are_frozen_and_six_scope_contrasts_are_appended():
    cases = load_calibration_cases()
    digest = hashlib.sha256(json.dumps([asdict(case) for case in cases[:49]],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == "21c2ca762e22e0f9e0213a78c92e48b50159147c605c8364567ee27734369f99"
    assert cases[49:55] == load_scope_contrasts()
    assert len(cases[:55]) == 55 and sum(len(case.expected) for case in cases[:55]) == 242


def test_truthful_extra_rating_list_violates_scope_without_factual_error():
    cases = _cases()
    short, expanded = cases["rating_existence_only"], cases["rating_existence_with_extra_list"]
    assert short.evidence == expanded.evidence and short.business_traces == expanded.business_traces
    assert expanded.answer.startswith(short.answer)
    assert short.expected == (True,) * 5
    assert expanded.expected == (True, False, True, True, True)
    main = next(case for case in load_cases() if case.id == "s18_absent_rating")
    assert main.turns[0].expect.semantic_checks[:2] == list(short.expectations)
    for case, expected_verdict in ((short, "pass"), (expanded, "fail")):
        # The third requirement forbids invented reasons; neither answer has one.
        decisions = _decisions((*case.expected[:2], True))
        # Frozen judge diagnostics do not grade task status. Exercise the current
        # main-suite delivery contract without rewriting those historic inputs.
        checks = grade_turn(main.turns[0].expect, _recorded_response(case, task_status="complete"), judgements=decisions)
        assert verdict(checks) == expected_verdict
        assert next(check for check in checks if check.name == "semantic_0").passed is True
        assert next(check for check in checks if check.name == "semantic_1").passed is case.expected[1]


def test_filter_claim_contrasts_preserve_count_and_distinguish_false_from_absent_evidence():
    cases = _cases()
    applied, not_applied, unknown = [cases[f"memory_count_filters_{suffix}"]
                                   for suffix in ("applied", "not_applied", "unknown")]
    assert applied.answer == not_applied.answer == unknown.answer
    assert applied.expectations == not_applied.expectations == unknown.expectations
    assert applied.expected == (True,) * 5
    assert not_applied.expected == (True, False, True, True, False)
    assert unknown.expected == (True, None, True, True, None)
    for case, value in ((applied, True), (not_applied, False)):
        trace = case.business_traces[0]
        assert trace["output"]["count"] == 5
        assert trace["input"]["exclude_st"] is trace["input"]["exclude_new"] is value
        assert trace["output"]["filter_audit"]["exclude_st_executed"] is value
    assert "exclude_st" not in unknown.business_traces[0]["input"]
    assert "filter_audit" not in unknown.business_traces[0]["output"]
    no_claim = cases["memory_count_filters_no_claim"]
    assert no_claim.evidence == not_applied.evidence
    assert "已剔除" not in no_claim.answer and no_claim.expected == (True,) * 5


@pytest.mark.parametrize("suffix,expected_verdict", [
    ("applied", "pass"), ("not_applied", "fail"), ("unknown", "review"), ("no_claim", "pass"),
])
def test_m17_filter_claims_propagate_to_verdict_without_overwriting_correct_count(suffix, expected_verdict):
    case = _cases()[f"memory_count_filters_{suffix}"]
    main = next(case for case in load_cases() if case.id == "m17_memory_is_not_fact")
    for turn_index, count in ((0, 8), (1, 5)):
        expect = main.turns[turn_index].expect
        assert expect.semantic_checks[1] == case.expectations[1]
        assert "上游血缘" in expect.semantic_checks[2]
        response = _recorded_response(case)
        if turn_index == 0:
            response.answer = response.answer.replace("沪深主板", "全市场").replace("5只", "8只")
            trace = response.tool_results[0]
            trace.input.pop("market")
            trace.output["market"] = "all"
            trace.output["count"] = count
            record = response.tool_results[-1].output["evidence"]["ev_count"]
            record["arguments"] = deepcopy(trace.input)
            record["payload"] = deepcopy(trace.output)
            record["rows"] = [deepcopy(trace.output)]
            response.tool_results[-2].output["tool_calls"][0]["args"]["answer"] = response.answer
        checks = grade_turn(expect, response, judgements=_decisions((*case.expected[:2], True)))
        assert verdict(checks) == expected_verdict
        assert next(check for check in checks if check.name == "semantic_0").passed is True
        assert next(check for check in checks if check.name == "semantic_1").passed is case.expected[1]
