from app.agent_eval.semantic_acceptance import semantic_samples
from langchain_core.messages import AIMessage

from app.agent_eval.full_answer_judge import calibration_samples, judge_full_answer


def test_semantic_calibration_has_positive_and_adversarial_examples():
    for adapter in ("clarify_missing_entity", "refuse_trade_guarantee"):
        samples = semantic_samples(adapter)
        assert len(samples) >= 6
        assert {item[2] for item in samples} == {"pass", "fail"}
        assert len({item[0] for item in samples}) == len(samples)


def test_full_answer_calibration_covers_support_conflict_and_abstention():
    evidence, samples = calibration_samples()
    assert len(evidence) >= 2
    assert len(samples) >= 10
    assert {item[2] for item in samples} == {"pass", "fail", "needs_review"}
    assert len({item[0] for item in samples}) == len(samples)


def test_full_answer_judge_requires_consistent_structured_verdict():
    class Provider:
        def generate_messages(self, messages, tools, **kwargs):
            return AIMessage(content="", tool_calls=[{
                "name": "submit_full_answer_judgment",
                "id": "judge",
                "args": {"verdict": "pass", "rationale": "全部有证据", "unsupported_or_conflicting_claims": []},
            }])

    judgment = judge_full_answer(Provider(), "问题", "回答", [])
    assert judgment.verdict == "pass"
