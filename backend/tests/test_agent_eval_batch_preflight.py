import pytest

from app.agent_eval.batch_preflight import approval_index


def approval(case_id="OFF-001"):
    return {"reviewer": "user_in_current_conversation",
            "approved_items": ["question_and_business_scope", "expected_business_facts", "scoring_rules"],
            "cases": [{"case_id": case_id, "case_version": 1, "case_digest": "case", "baseline_digest": "world"}],
            "rejected_cases": [{"case_id": "OFF-999"}]}


def test_approval_index_ignores_rejections_and_rejects_duplicates():
    indexed = approval_index([approval()])
    assert set(indexed) == {"OFF-001"}
    with pytest.raises(ValueError, match="duplicate"):
        approval_index([approval(), approval()])


def test_approval_index_requires_all_three_business_items():
    record = approval()
    record["approved_items"].pop()
    with pytest.raises(ValueError, match="incomplete"):
        approval_index([record])
