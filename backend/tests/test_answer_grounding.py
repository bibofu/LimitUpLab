import unittest

from app.agents.answer_grounding import evaluate_answer_grounding
from app.models import AgentToolTrace


def _trace(*, status: str = "success", output: dict | None = None) -> AgentToolTrace:
    return AgentToolTrace(
        name="stock_facts",
        input={},
        summary="test evidence",
        status=status,
        output=output or {},
        error="upstream unavailable" if status == "error" else None,
    )


class AnswerGroundingTest(unittest.TestCase):
    def test_structured_claims_support_formatting_and_unit_conversion(self) -> None:
        result = evaluate_answer_grounding(
            (
                "2026-05-15 共 2 只，分别是三花智控(002050)；"
                "评分 58.1，置信度 52%，成交额 5.2亿元，"
                "首封 13:11，炸板 3 次。"
            ),
            [
                _trace(
                    output={
                        "trade_date": "2026-05-15",
                        "matched_count": 2,
                        "events": [
                            {
                                "symbol": "002050",
                                "name": "三花智控",
                                "confidence": 0.52,
                                "score": 58.1,
                                "amount": 520_000_000,
                                "first_limit_time": "13:11:00",
                                "break_count": 3,
                            }
                        ],
                    }
                )
            ],
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.claim_count, 8)
        self.assertEqual(result.supported_claim_count, 8)
        self.assertEqual(result.claim_support_rate, 1.0)
        self.assertTrue(all(claim.evidence_paths for claim in result.claims))

    def test_unsupported_entity_and_number_are_reported_individually(self) -> None:
        result = evaluate_answer_grounding(
            "幻觉股份(600000) 的涨幅是 99%。",
            [
                _trace(
                    output={
                        "events": [
                            {
                                "symbol": "002050",
                                "name": "三花智控",
                                "change_pct": 9.98,
                            }
                        ]
                    }
                )
            ],
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.unsupported_claim_count, 2)
        self.assertEqual(
            {claim.kind for claim in result.claims if not claim.supported},
            {"stock_entity", "number"},
        )

    def test_user_supplied_claim_is_not_treated_as_agent_hallucination(self) -> None:
        result = evaluate_answer_grounding(
            "目前无法确认 2026-05-15 的 600000。",
            [_trace(status="error")],
            user_message="查询 2026-05-15 的 600000",
        )

        self.assertEqual(result.claim_count, 0)
        self.assertFalse(result.tool_failure_hallucination)

    def test_tool_failure_hallucination_and_over_refusal_are_distinct(self) -> None:
        hallucination = evaluate_answer_grounding(
            "工具失败，但我确认三花智控(002050)上涨 8%。",
            [_trace(status="error")],
        )
        refusal = evaluate_answer_grounding(
            "暂不支持，无法回答。",
            [_trace(output={"matched_count": 2})],
        )

        self.assertTrue(hallucination.tool_failure_hallucination)
        self.assertFalse(hallucination.over_refusal)
        self.assertTrue(refusal.over_refusal)
        self.assertFalse(refusal.tool_failure_hallucination)


if __name__ == "__main__":
    unittest.main()
