from app.agent_eval.formal_report import (
    _accepted_candidate_digest, _full_answer_verdict, _percentile, compare_formal_reports,
)
from app.agent_eval.recorder import digest
from pydantic import BaseModel


def test_formal_latency_percentiles_use_observed_values():
    values = list(range(1, 31))
    assert _percentile(values, .5) == 16
    assert _percentile(values, .95) == 29


def test_active_case_maps_back_to_reviewed_candidate_digest():
    class CaseBinding(BaseModel):
        status: str
        content: str

    case = CaseBinding(status="active", content="unchanged reviewed contract")
    candidate = case.model_copy(update={"status": "candidate"})

    assert _accepted_candidate_digest(case) == digest(candidate.model_dump(mode="json"))


def test_full_answer_never_masks_a_core_failure():
    assert _full_answer_verdict("fail", "pass") == "fail"
    assert _full_answer_verdict("pass", "pass") == "pass"
    assert _full_answer_verdict("pass", "fail") == "fail"
    assert _full_answer_verdict("pass", "needs_review") == "needs_review"


def test_formal_diff_backfills_v1_full_answer_rate_and_lists_case_changes():
    previous = {
        "schema_version": "formal-agent-baseline-v1", "suite_id": "local30", "case_count": 2,
        "counts": {"pass": 1, "fail": 1, "needs_review": 0, "unscorable": 0},
        "core_contract_pass_rate": .5, "terminal_accuracy": .5, "factual_core_accuracy": 1.0,
        "semantic_answer_accuracy": 1.0, "infrastructure_success_rate": 1.0,
        "cases": [
            {"case_id": "A", "core_contract_verdict": "pass", "terminal_verdict": "pass",
             "full_answer_verdict": "needs_review"},
            {"case_id": "B", "core_contract_verdict": "fail", "terminal_verdict": "fail",
             "full_answer_verdict": "fail"},
        ],
    }
    current = {
        **previous, "schema_version": "formal-agent-baseline-v3",
        "counts": {"pass": 2, "fail": 0, "needs_review": 0, "unscorable": 0},
        "core_contract_pass_rate": 1.0, "terminal_accuracy": 1.0, "full_answer_pass_rate": 1.0,
        "cases": [
            {"case_id": "A", "core_contract_verdict": "pass", "terminal_verdict": "pass",
             "full_answer_verdict": "pass"},
            {"case_id": "B", "core_contract_verdict": "pass", "terminal_verdict": "pass",
             "full_answer_verdict": "pass"},
        ],
    }

    comparison = compare_formal_reports(previous, current)

    assert comparison["metrics"]["full_answer_pass_rate"] == {"before": 0.0, "after": 1.0, "delta": 1.0}
    assert comparison["metrics"]["terminal_accuracy"]["delta"] == .5
    assert [item["case_id"] for item in comparison["case_changes"]] == ["A", "B"]
