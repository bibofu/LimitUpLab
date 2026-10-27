"""Calibration metrics for the safe_auto live case runner."""

from scripts.evaluate_memory_auto import summarize


def test_summary_counts_false_positives_and_negatives():
    summary = summarize([
        {"expected": True, "actual": True, "decision": "auto_activate"},
        {"expected": True, "actual": False, "decision": "hold"},
        {"expected": False, "actual": False, "decision": "hold"},
        {"expected": False, "actual": True, "decision": "auto_activate"},
        {"expected": False, "actual": False, "decision": "error"},
    ])
    assert summary == {
        "total": 5,
        "true_positive": 1,
        "true_negative": 2,
        "false_positive": 1,
        "false_negative": 1,
        "errors": 1,
        "accuracy": 0.6,
    }
