"""Metrics must expose unknown coverage and distinguish content from formatting."""

from evals.golden.dimensions import dimension_counts
from evals.golden.reporting import summarize


def trial(checks, *, verdict="fail", number=1):
    return {"case_id": "a", "category": "multi", "family": "memory_compaction",
            "trial": number, "verdict": verdict, "completed": True, "duration_seconds": 1,
            "turns": [{"checks": [{"name": name, "passed": value} for name, value in checks]}]}


def test_extra_columns_do_not_erase_correct_projected_values():
    result = trial([("expected_values", True), ("table_fields", False), ("expected_rows", False)])
    counts = dimension_counts([result])
    assert counts["facts"]["pass"] == 1
    assert counts["delivery"]["fail"] == 1
    assert counts["delivery"]["turns"] == 1  # Not two votes for two failed checks.
    assert "safety" not in counts


def test_unknown_values_and_fixture_gaps_cannot_be_reported_as_passes():
    unknown = trial([("expected_values", None), ("table_evidence_values", True),
                     ("semantic_0", None)], verdict="review")
    gap = trial([("expected_values", True)], verdict="harness_error", number=2)
    counts = dimension_counts([unknown, gap])
    assert counts["facts"] == {"turns": 2, "pass": 0, "fail": 0, "review": 1,
                               "harness_error": 1, "pass_rate": 0}
    assert counts["semantic"]["review"] == 1


def test_diagnostics_do_not_inflate_scores_and_unrecognized_checks_are_visible():
    counts = dimension_counts([trial([("visible_draft_record", True), ("new_unknown_check", None)])])
    assert list(counts) == ["other"]
    assert counts["other"]["review"] == 1


def test_memory_family_is_reported_as_task_results_not_memory_accuracy():
    result = trial([("expected_values", True), ("table_only", False)])
    summary = summarize([result], planned_trials=1, planned_cases=1)
    assert summary["completed_cases"] == 1
    assert summary["fully_repeated_cases"] == 0
    assert summary["by_family"]["memory_compaction"]["verdict_counts"] == {"fail": 1}
    assert summary["by_dimension"]["facts"]["pass_rate"] == 1


def test_source_and_visible_facts_are_independent_of_structured_values():
    counts = dimension_counts([trial([("expected_values", True), ("table_evidence_values", True),
        ("visible_factual_grounding", None), ("visible_source_attribution", False),
        ("visible_answer_safety", True)])])
    assert counts["facts"]["pass"] == 1
    assert counts["visible_facts"]["review"] == 1
    assert counts["source_attribution"]["fail"] == 1
    assert counts["safety"]["pass"] == 1


def test_judge_stage_errors_include_preserved_interrupted_attempts():
    latest = trial([("visible_source_attribution", None)], verdict="review")
    latest["turns"][0]["judge_errors"] = {"visible_audit": "ValidationError"}
    interrupted = trial([], verdict="harness_error")
    interrupted["turns"][0]["judge_errors"] = {"final_delivery": "BudgetExceeded"}
    summary = summarize([latest], planned_trials=1, planned_cases=1, attempt_history=[interrupted])
    assert summary["judge_stage_errors"] == {
        "final_delivery:BudgetExceeded": 1, "visible_audit:ValidationError": 1}
    assert summary["judge_phase_protocol_errors"] == {"visible_audit:ValidationError": 1}
    assert summary["judge_dimension_error_count"] == 0
    assert summary["judge_phase_protocol_error_rate"] is None  # Legacy reports have no observed denominator.
    assert summary["verdict_counts"] == {"review": 1}
