"""Tests for the Markdown Agent question-bank runner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_agent_question_bank import parse_question_bank, render_markdown


class AgentQuestionBankRunnerTest(unittest.TestCase):
    def test_claude_question_bank_parses_all_numbered_questions(self) -> None:
        questions = parse_question_bank(BACKEND_ROOT.parent / "testQuestion.md")

        self.assertEqual(len(questions), 100)
        self.assertEqual(questions[0].number, 1)
        self.assertEqual(questions[0].text, "你好，你能做什么？")
        self.assertEqual(questions[0].expected_capability, "capability_intro")
        self.assertEqual(questions[-1].number, 100)
        self.assertIn("评分系统", questions[-1].text)

    def test_markdown_report_keeps_complete_answer_and_latency(self) -> None:
        report = {
            "started_at": "2026-09-03T09:00:00+00:00",
            "updated_at": "2026-09-03T09:00:01+00:00",
            "mode": "live-llm",
            "model": "test-model",
            "question_bank": "testQuestion.md",
            "question_count": 100,
            "summary": {
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "average_wall_duration_ms": 1234,
                "p50_wall_duration_ms": 1234,
                "p95_wall_duration_ms": 1234,
                "total_wall_duration_ms": 1234,
            },
            "results": [
                {
                    "number": 1,
                    "section": "能力探索",
                    "question": "你好，你能做什么？",
                    "expected_capability": "capability_intro",
                    "status": "success",
                    "intent": "capability_intro",
                    "tool_calls": [],
                    "wall_duration_ms": 1234,
                    "performance": {
                        "planner_duration_ms": 100,
                        "tool_duration_ms": 200,
                        "answer_duration_ms": 900,
                        "total_duration_ms": 1200,
                    },
                    "warnings": [],
                    "answer": "这是完整回答。",
                }
            ],
        }

        rendered = render_markdown(report)

        self.assertIn("这是完整回答。", rendered)
        self.assertIn("墙钟耗时：1.234s", rendered)
        self.assertIn("Planner 0.100s", rendered)


if __name__ == "__main__":
    unittest.main()
