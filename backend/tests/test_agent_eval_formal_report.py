from app.agent_eval.formal_report import _accepted_candidate_digest, _full_answer_verdict, _percentile
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
