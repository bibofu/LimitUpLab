import unittest
from datetime import date

from app.agents.review_agent import build_review_agent_report
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class FakeReviewLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "planner" in system_prompt.lower():
            return LLMResult(
                content=(
                    '{"tool_calls":['
                    '{"name":"daily_high_score_picks"},'
                    '{"name":"pick_outcomes"},'
                    '{"name":"compare_success_failure_features"}'
                    "]}"
                ),
                model="fake-review",
                provider="fake",
            )
        return LLMResult(
            content=(
                '{"main_findings":["高分首板样本需要持续追踪兑现率"],'
                '"successful_patterns":["成功样本通常有更强后续高点"],'
                '"failed_patterns":["失败样本需要复盘市场环境和题材持续性"],'
                '"scoring_bias":["可能高估了首封时间"],'
                '"adjustment_suggestions":["降低弱市场环境下的置信度"],'
                '"confidence":0.77}'
            ),
            model="fake-review",
            provider="fake",
        )


class ReviewAgentTest(unittest.TestCase):
    def test_review_agent_uses_llm_planner_and_tools(self) -> None:
        provider = FakeReviewLLMProvider()

        report = build_review_agent_report(
            events=SAMPLE_EVENTS,
            start_date=date(2026, 5, 15),
            end_date=date(2026, 5, 15),
            min_score=70,
            provider=provider,
        )

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(report.generated_by, "review-agent-tool-use-v1")
        self.assertEqual(report.confidence, 0.77)
        self.assertTrue(report.main_findings)
        self.assertEqual(
            [trace.name for trace in report.tool_results],
            [
                "daily_high_score_picks",
                "pick_outcomes",
                "compare_success_failure_features",
            ],
        )


if __name__ == "__main__":
    unittest.main()
