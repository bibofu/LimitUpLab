"""Calibration fixtures are engineering expectations, never claimed human labels."""

import json

from evals.quality_judge.calibrate import diagnostic_metrics, load_samples, run_calibration, sample_bundle
from test_quality_judge import ScriptedProvider, message, passing


def test_synthetic_development_and_heldout_splits_have_explicit_provenance():
    development, holdout = load_samples(split="development"), load_samples(split="holdout")
    assert len(development["samples"]) == len(holdout["samples"]) == 6
    assert development["sample_set_hash"] == holdout["sample_set_hash"]
    assert not development["human_calibrated"] and not holdout["human_calibrated"]
    assert not ({item["id"] for item in development["samples"]} & {item["id"] for item in holdout["samples"]})
    for sample in development["samples"] + holdout["samples"]:
        bundle = sample_bundle(sample)
        assert bundle["request"]["message"] == sample["question"]
        assert "expected" not in json.dumps(bundle)


def test_diagnostic_metrics_separate_protocol_failures_unknown_and_detection_errors():
    def record(expected, observed, status="succeeded"):
        return {"expected": {"factual_grounding": expected}, "report": {"execution_status": status,
                "checks": [{"dimension": "factual_grounding", "verdict": observed}]}}
    result = diagnostic_metrics([record("pass", "fail"), record("fail", "unknown"),
                                 record("unknown", "unknown"), record("fail", "unknown", "error")])
    assert result == {"samples": 4, "execution_failures": 1, "comparisons": 3,
                      "matched": 1, "false_fail": 1, "missed_fail": 1, "unknown": 2}


def test_calibration_runner_never_includes_expected_labels_in_judge_input():
    provider = ScriptedProvider(*(message(passing()) for _ in range(6)))
    result = run_calibration(provider, split="development", max_calls=6)
    assert result["call_count"] == 6 and result["metrics"]["samples"] == 6
    assert result["human_calibrated"] is False
    assert all('"expected"' not in messages[1].content for messages, _, _ in provider.calls)
    assert all(record["usage"]["total_tokens"] is None for record in result["records"])
