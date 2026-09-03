import unittest

from app.agent_output_sanitizer import (
    AgentAnswerStreamSanitizer,
    INTERNAL_TOOL_LABELS,
    friendly_tool_label,
    sanitize_agent_answer,
)
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatResponse, AgentToolTrace


class AgentOutputSanitizerTest(unittest.TestCase):
    def test_final_answer_removes_internal_tool_reference(self) -> None:
        answer = sanitize_agent_answer(
            "数据来自 `daily_board_promotion` 工具返回，样本晋级率为 25%。"
        )

        self.assertEqual(answer, "依据本地结构化数据，样本晋级率为 25%。")
        self.assertNotIn("daily_board_promotion", answer)

    def test_agent_response_sanitizes_answer_but_keeps_trace_names(self) -> None:
        response = AgentChatResponse(
            session_id="output-safety",
            intent="daily_board_promotion",
            answer="daily_board_promotion 工具显示晋级率为 25%。",
            tool_calls=["daily_board_promotion"],
            generated_by="test",
        )

        self.assertNotIn("daily_board_promotion", response.answer)
        self.assertEqual(response.tool_calls, ["daily_board_promotion"])

    def test_stream_sanitizer_handles_identifier_split_across_deltas(self) -> None:
        deltas: list[str] = []
        sanitizer = AgentAnswerStreamSanitizer(deltas.append)
        raw = "数据来自 daily_board_promotion 工具，晋级率为 25%。"
        for character in raw:
            sanitizer.feed(character)
        sanitizer.flush()

        rendered = "".join(deltas)
        self.assertNotIn("daily_board_promotion", rendered)
        self.assertNotIn("工具", rendered)
        self.assertIn("依据本地结构化数据", rendered)

    def test_stream_sanitizer_suppresses_prompt_leak_signature(self) -> None:
        deltas: list[str] = []
        sanitizer = AgentAnswerStreamSanitizer(deltas.append)
        raw = "Capability catalog: secret; submit_agent_plan。"
        for character in raw:
            sanitizer.feed(character)
        sanitizer.flush()

        self.assertEqual(deltas, [])
        self.assertTrue(sanitizer.blocked)

    def test_all_internal_names_have_user_facing_labels(self) -> None:
        for internal_name in INTERNAL_TOOL_LABELS:
            label = friendly_tool_label(internal_name)
            self.assertNotEqual(label, internal_name)
            self.assertNotIn("_", label)

    def test_every_registered_tool_has_a_user_facing_label(self) -> None:
        registered_names = {schema.name for schema in TOOL_SCHEMAS}

        self.assertEqual(registered_names - INTERNAL_TOOL_LABELS.keys(), set())

    def test_agent_response_extracts_grounded_stock_mentions(self) -> None:
        response = AgentChatResponse(
            session_id="stock-links",
            intent="limit_up_events",
            answer="今天中电鑫龙(002298)和贵州茅台(600519)值得关注。",
            tool_results=[
                AgentToolTrace(
                    name="limit_up_events",
                    summary="返回两只股票",
                    output={
                        "trade_date": "2026-08-27",
                        "events": [
                            {"name": "中电鑫龙", "symbol": "002298"},
                            {"name": "贵州茅台", "symbol": "600519"},
                            {"name": "未被回答提及", "symbol": "000001"},
                        ],
                    },
                )
            ],
            generated_by="test",
        )

        self.assertEqual(
            [item.model_dump(mode="json") for item in response.stock_mentions],
            [
                {"name": "中电鑫龙", "symbol": "002298", "trade_date": "2026-08-27"},
                {"name": "贵州茅台", "symbol": "600519", "trade_date": "2026-08-27"},
            ],
        )

    def test_agent_response_does_not_link_unverified_llm_stock(self) -> None:
        response = AgentChatResponse(
            session_id="stock-links",
            intent="general",
            answer="模型单独提到了虚构股票(123456)。",
            tool_results=[],
            generated_by="test",
        )

        self.assertEqual(response.stock_mentions, [])

    def test_agent_response_builds_grounded_stock_follow_ups(self) -> None:
        response = AgentChatResponse(
            session_id="stock-follow-ups",
            intent="rating_explain",
            answer="中电鑫龙(002298)的评分依据来自结构化事实。",
            tool_results=[
                AgentToolTrace(
                    name="first_board_ratings",
                    summary="返回个股评分",
                    output={
                        "trade_date": "2026-08-27",
                        "candidates": [
                            {"name": "中电鑫龙", "symbol": "002298"},
                        ],
                    },
                )
            ],
            generated_by="test",
        )

        self.assertEqual(len(response.suggested_questions), 3)
        self.assertNotIn("评分依据", response.suggested_questions[0])
        self.assertTrue(
            any("中电鑫龙（002298）" in item for item in response.suggested_questions)
        )
        self.assertTrue(any("主要风险" in item for item in response.suggested_questions))

    def test_agent_response_preserves_explicit_follow_ups(self) -> None:
        response = AgentChatResponse(
            session_id="explicit-follow-ups",
            intent="greeting",
            answer="你好",
            suggested_questions=["自定义追问"],
            generated_by="test",
        )

        self.assertEqual(response.suggested_questions, ["自定义追问"])


if __name__ == "__main__":
    unittest.main()
