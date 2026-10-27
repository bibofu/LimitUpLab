"""Safe-auto observability aggregation over committed extraction results."""

import json

from scripts.report_memory_auto import summarize_job_results


def test_summary_aggregates_decisions_without_content():
    results = [
        json.dumps({
            "auto_mode": "safe_auto",
            "auto_policy_version": "safe-auto-v1",
            "auto_decisions": [
                {"candidate_id": "a", "outcome": "activated",
                 "reasons": ["structured_tool_display_add"]},
                {"candidate_id": "b", "outcome": "judge_held",
                 "reasons": ["not_an_upsert", "low_confidence"],
                 "judge_decision": "hold", "judge_risk": "medium"},
            ],
        }),
        json.dumps({
            "auto_mode": "safe_auto_shadow",
            "auto_policy_version": "safe-auto-v1",
            "auto_activated_ids": [],
            "auto_shadow_eligible_ids": ["c"],
            "auto_held_count": 1,
        }),
        "not json",
    ]
    summary = summarize_job_results(results)
    assert summary["jobs"] == 2
    assert summary["modes"] == {"safe_auto": 1, "safe_auto_shadow": 1}
    assert summary["outcomes"] == {"activated": 1, "judge_held": 1, "shadow": 1, "held": 1}
    assert summary["reasons"]["not_an_upsert"] == 1
    assert summary["judge_decisions"] == {"hold": 1}
    assert summary["judge_risks"] == {"medium": 1}
    assert "content_text" not in json.dumps(summary)
