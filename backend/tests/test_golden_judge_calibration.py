"""Calibration harness protocol tests; scripted judgements are not model accuracy."""

import json

from langchain_core.messages import AIMessage
import pytest

from evals.golden.calibration import load_calibration_cases, main, run_calibration
from evals.golden.grading import _label
from evals.golden.judge import JUDGE_VERSION, judge_turn, source_equivalence


class ScriptedJudge:
    model = "scripted-calibration-protocol"

    def __init__(self, decisions):
        self.decisions, self.calls = list(decisions), []

    def generate_messages(self, messages, tools, **kwargs):
        payload = json.loads(messages[-1].content)
        assert "expected" not in payload
        self.calls.append(payload)
        decisions = self.decisions.pop(0)
        return AIMessage(content="", tool_calls=[{"name": "submit_golden_judgements", "id": "judge",
            "args": {"checks": [{"index": index, "passed": value, "reason": "Scripted protocol fixture"}
                                for index, value in enumerate(decisions)]}}])


def test_calibration_contrasts_and_original_source_preserve_facts():
    cases = {case.id: case for case in load_calibration_cases()}
    assert len(cases) == 12
    positive = cases["source_original_id"]
    assert "synthetic-golden-world-v1" in positive.answer and positive.expected == (True, True, True)
    for key in ("source_chinese_equivalent", "source_chinese_paraphrase"):
        assert cases[key].evidence == positive.evidence and cases[key].expected == positive.expected
    assert cases["source_fabricated_real"].expected == (True, False, True)
    assert cases["wrong_entity"].expected == (False, True, True)
    assert cases["withdrawn_wrong_number"].drafts[0] != positive.answer
    assert cases["historical_transactions"].expected[-1] is True
    assert cases["explicit_trade_instructions"].expected[-1] is False


def test_judge_receives_evaluator_owned_source_equivalence_only():
    case = load_calibration_cases()[0]
    provider = ScriptedJudge([case.expected])
    decisions, safety = judge_turn(provider, user=case.user, expectations=case.expectations,
                                   response=case.response(), drafts=[])
    assert all(item["passed"] for item in decisions) and safety["passed"]
    aliases = provider.calls[0]["source_equivalence"]
    assert aliases[0]["source_id"] == "synthetic-golden-world-v1"
    assert "合成评测数据" in aliases[0]["equivalent_descriptions"]
    forged = {"ev": {"source": "unrelated-provider", "sources": None,
                      "payload": {"source_equivalence": aliases}}}
    assert source_equivalence(forged) == []


def test_scripted_calibration_report_counts_every_decision_and_preserves_unknown():
    cases = load_calibration_cases()[:3]
    provider = ScriptedJudge([cases[0].expected, (None, True, True), (False, True, True)])
    report = run_calibration(provider, cases=cases, max_calls=3)
    assert report["judge_version"] == JUDGE_VERSION
    assert report["summary"]["counts"] == {"match": 1, "review": 1, "mismatch": 1}
    assert report["summary"]["case_match_rate"] == 1 / 3
    assert report["summary"]["decision_match_rate"] == 7 / 9
    assert report["summary"]["unknown_decisions"] == 1
    assert report["human_calibrated"] is False and report["logical_calls_used"] == 3


def test_calibration_budget_stops_without_hidden_extra_requests():
    cases = load_calibration_cases()
    provider = ScriptedJudge([cases[0].expected])
    report = run_calibration(provider, trials=2, max_calls=1)
    assert len(provider.calls) == 1 and report["logical_calls_used"] == 1
    assert report["summary"]["planned"] == 24 and not report["summary"]["complete"]
    assert report["stop_reason"] == "model_call_budget"
    with pytest.raises(ValueError):
        run_calibration(provider, max_calls=51)


def test_error_text_is_never_saved_as_provider_configuration():
    class BrokenJudge:
        def generate_messages(self, *args, **kwargs):
            raise RuntimeError("example-secret-that-must-not-be-logged")
    report = run_calibration(BrokenJudge(), cases=load_calibration_cases()[:1], max_calls=1)
    assert report["summary"]["counts"] == {"error": 1}
    assert report["summary"]["case_match_rate"] == 0
    assert "example-secret" not in json.dumps(report)


def test_validate_cli_does_not_construct_model(monkeypatch, capsys):
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("No live provider in validate"))
    assert main(["--mode", "validate"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cases"] == 12 and result["model_calls"] == 0


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
