import unittest

from app.agents.explanation import explain_first_board_rating
from app.agents.first_board import build_first_board_ratings
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class FakeLLMProvider(LLMProvider):
    def __init__(self, content: str):
        self.content = content

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content=self.content,
            model="fake-model",
            provider="fake-provider",
        )


class BrokenLLMProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        raise RuntimeError("offline")


class ExplanationAgentTest(unittest.TestCase):
    def test_falls_back_to_template_when_llm_unavailable(self) -> None:
        rating = build_first_board_ratings(SAMPLE_EVENTS).candidates[0]

        result = explain_first_board_rating(
            rating=rating,
            provider=BrokenLLMProvider(),
        )

        self.assertEqual(result.source, "template")
        self.assertIn("template_explanation", result.tool_calls)
        self.assertIn(rating.facts.symbol, result.answer)
        self.assertNotIn("历史相似", result.answer)
        self.assertTrue(result.warnings)

    def test_accepts_safe_llm_output_and_adds_boundary(self) -> None:
        rating = build_first_board_ratings(SAMPLE_EVENTS).candidates[0]

        result = explain_first_board_rating(
            rating=rating,
            provider=FakeLLMProvider("这是基于结构化事实的解释。"),
        )

        self.assertEqual(result.source, "fake-provider:fake-model")
        self.assertIn("llm_explanation", result.tool_calls)
        self.assertIn("不构成买卖建议", result.answer)

    def test_unsafe_llm_output_uses_template_fallback(self) -> None:
        rating = build_first_board_ratings(SAMPLE_EVENTS).candidates[0]

        result = explain_first_board_rating(
            rating=rating,
            provider=FakeLLMProvider("建议买入并设置目标价。"),
        )

        self.assertEqual(result.source, "template")
        self.assertNotIn("建议买入", result.answer)


if __name__ == "__main__":
    unittest.main()
