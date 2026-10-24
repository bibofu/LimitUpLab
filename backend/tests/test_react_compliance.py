from langchain_core.messages import AIMessage
import pytest

from app.agents.compliance import review_answer


class Provider:
    def __init__(self, args):
        self.args = args

    def generate_messages(self, messages, tools, **kwargs):
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "submit_compliance_review"
        assert "candidate_answer" in messages[-1].content
        return AIMessage(content="", tool_calls=[{
            "id": "review", "name": "submit_compliance_review", "args": self.args,
        }])


def test_structured_compliance_review_accepts_research_answer():
    result = review_answer(
        Provider({"decision": "allow", "violations": [], "reason": "historical fact"}),
        user_message="解释龙虎榜",
        answer="机构历史净买入金额为1亿元。",
        timeout_seconds=10,
    )
    assert result.decision == "allow"


def test_structured_compliance_review_rejects_implicit_trade_instruction():
    result = review_answer(
        Provider({
            "decision": "reject",
            "violations": ["trade_instruction"],
            "reason": "direct participation advice",
        }),
        user_message="现在能不能参与",
        answer="这只股票值得参与。",
        timeout_seconds=10,
    )
    assert result.violations == ["trade_instruction"]


def test_truncated_compliance_verdict_cannot_authorize_publication():
    class TruncatedReview(Provider):
        def generate_messages(self, messages, tools, **kwargs):
            result = super().generate_messages(messages, tools, **kwargs)
            result.response_metadata["finish_reason"] = "length"
            return result

    with pytest.raises(ValueError, match="did not finish"):
        review_answer(
            TruncatedReview({"decision": "allow", "violations": [], "reason": "partial"}),
            user_message="解释历史事实", answer="不构成任何仓位建议。", timeout_seconds=10,
        )
