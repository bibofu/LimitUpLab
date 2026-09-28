"""Group observed checks without treating format failures as factual errors."""

from collections import Counter, defaultdict

GROUPS = {
    "facts": {"expected_values", "table_evidence_values", "computed_rows"},
    "delivery": {"table_count", "table_declaration", "table_shape", "table_fields",
                 "table_headers", "column_meaning", "duplicate_rows", "expected_rows",
                 "table_only", "no_placeholder", "stream_protocol", "visible_table_only"},
    "evidence": {"citations", "evidence_required", "evidence_lineage", "evidence_exists",
                 "evidence_scope", "evidence_identity", "evidence_usable", "evidence_payload_rows",
                 "compute_parents", "compute_operation", "original_tool_payload", "evidence_structure",
                 "evidence_dates", "empty_evidence", "complete_source"},
    "task_state": {"status", "finish_status", "missing_consistency", "finish_submission"},
    "tool_use": {"forbidden_tools", "tool_budget"},
    "safety": {"visible_answer_safety"},
}
DIAGNOSTICS = {"visible_draft_record"}


def check_dimension(name):
    if name in DIAGNOSTICS:
        return None
    if name.startswith("semantic_"):
        return "semantic"
    return next((group for group, names in GROUPS.items() if name in names), "other")


def dimension_counts(results):
    """One vote per turn/dimension, not per check or rendered row.

    No assertion means not evaluated, never a pass. A fixture gap prevents the
    enclosing trial from claiming capability scores, while raw checks survive.
    """
    totals = defaultdict(Counter)
    for trial in results:
        for turn in trial.get("turns", []):
            grouped = defaultdict(list)
            for check in turn.get("checks", []):
                group = check_dimension(check["name"])
                if group is not None:
                    grouped[group].append(check.get("passed"))
            for group, values in grouped.items():
                if trial["verdict"] == "harness_error":
                    outcome = "harness_error"
                elif any(value is False for value in values):
                    outcome = "fail"
                elif any(value is not True for value in values):
                    outcome = "review"
                else:
                    outcome = "pass"
                totals[group][outcome] += 1
    return {group: {"turns": sum(counts.values()), **{
        name: counts[name] for name in ("pass", "fail", "review", "harness_error")},
        "pass_rate": counts["pass"] / sum(counts.values())}
        for group, counts in sorted(totals.items())}
