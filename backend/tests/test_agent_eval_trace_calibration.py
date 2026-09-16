import json

from langchain_core.messages import AIMessage
import pytest

from app.agent_eval.trace_calibration import calibration_samples, calibrate_trace_judge, summarize
from app.agent_eval.trace_review import TraceJudgment


class Provider:
    model = "synthetic-only"

    def __init__(self, broken=False, suite="core"):
        self.calls = 0
        self.broken = broken
        self.suite = suite

    def generate_messages(self, messages, tools, **kwargs):
        sample = calibration_samples(self.suite)[self.calls]
        self.calls += 1
        assert json.loads(messages[1].content) == sample["packet"]
        if self.broken:
            raise ValueError("synthetic protocol failure")
        dimensions = {}
        for key in TraceJudgment.model_fields:
            verdict = sample["expected"].get(key, "pass")
            dimensions[key] = {"verdict": verdict, "rationale": "synthetic label",
                               "issue": "specific issue" if verdict == "fail" else "",
                               "evidence_ids": ["e1"]}
        return AIMessage(content="", tool_calls=[{"id": "j", "name": "submit_trace_review",
                                                  "args": dimensions}],
                         usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})


def test_calibration_uses_production_protocol_and_preserves_artifacts(tmp_path):
    provider = Provider()
    destination = tmp_path / "calibration"
    report = calibrate_trace_judge(destination, provider)
    assert provider.calls == report["model_calls"] == 6
    assert report["all_labels_matched"]
    assert report["total_tokens"] == 720
    assert not report["independent_acceptance"] and not report["release_eligible"]
    assert len(list((destination / "calls").glob("*-request.json"))) == 6
    with pytest.raises(FileExistsError):
        calibrate_trace_judge(destination, provider)
    assert provider.calls == 6


def test_errors_do_not_retry_or_disappear(tmp_path):
    provider = Provider(broken=True)
    report = calibrate_trace_judge(tmp_path / "errors", provider)
    assert provider.calls == 6
    assert not report["all_labels_matched"]
    assert report["total_tokens"] is None
    assert all(r["status"] == "judge_error" for r in report["results"])


def test_confusion_metrics_separate_abstention_from_wrong_labels():
    rows = [{"expected": {"grounding": expected}, "actual": {"grounding": actual}}
            for expected, actual in [("pass", "fail"), ("fail", "pass"), ("fail", "needs_review")]]
    metrics = summarize(rows)["grounding"]
    assert metrics["false_fail"] == metrics["false_pass"] == metrics["abstained_or_unscored"] == 1
    assert metrics["false_fail_rate"] == 1
    assert metrics["false_pass_rate"] == 0.5


def test_output_constraint_pairs_and_bounded_suite(tmp_path):
    samples = calibration_samples("output_constraints")
    strict, extra, ordinary, missing = samples
    assert strict["packet"]["evidence"] == extra["packet"]["evidence"]
    assert extra["packet"]["answer"] == ordinary["packet"]["answer"]
    assert extra["expected"]["task_completion"] == "fail"
    assert ordinary["expected"]["task_completion"] == "pass"
    assert missing["expected"]["task_completion"] == "pass"
    provider = Provider(suite="output_constraints")
    report = calibrate_trace_judge(tmp_path / "constraints", provider, suite="output_constraints")
    assert report["all_labels_matched"]
    assert report["model_calls"] == 4
    assert report["suite"] == "output_constraints"
    assert report["metrics"]["task_completion"]["labeled"] == 4


def test_invalid_suite_does_not_create_artifacts(tmp_path):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="unknown"):
        calibrate_trace_judge(output, Provider(), suite="unknown")
    assert not output.exists()
