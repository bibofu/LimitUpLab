import unittest

from app.agents.chat import _has_usable_tool_facts, _tool_outcome_warnings
from app.models import AgentToolOutcome, AgentToolTrace, build_agent_evidence_cards


class AgentToolOutcomeTest(unittest.TestCase):
    def test_successful_empty_payload_is_not_a_tool_error(self) -> None:
        trace = AgentToolTrace(
            name="limit_up_events",
            input={},
            summary="no matching rows",
            output={"data_fresh": True, "matched_count": 0, "events": []},
        )

        self.assertIsNotNone(trace.result)
        assert trace.result is not None
        self.assertEqual(trace.result.status, "empty")
        self.assertTrue(trace.result.data_fresh)
        self.assertEqual(trace.result.source_errors, [])
        self.assertEqual(trace.result.payload, trace.output)
        serialized = trace.model_dump(mode="json")
        self.assertEqual(serialized["result"]["status"], "empty")
        self.assertEqual(serialized["result"]["payload"], trace.output)

    def test_partial_payload_preserves_source_errors(self) -> None:
        trace = AgentToolTrace(
            name="stock_activity",
            input={},
            summary="partial facts",
            output={
                "data_fresh": False,
                "items": [{"symbol": "002050"}],
                "source_errors": ["news source unavailable"],
            },
        )

        assert trace.result is not None
        self.assertEqual(trace.result.status, "partial")
        self.assertFalse(trace.result.data_fresh)
        self.assertEqual(trace.result.source_errors, ["news source unavailable"])

    def test_legacy_error_trace_is_normalized_to_error_outcome(self) -> None:
        trace = AgentToolTrace(
            name="stock_kline",
            input={},
            summary="failed",
            status="error",
            error="provider timeout",
        )

        assert trace.result is not None
        self.assertEqual(trace.result.status, "error")
        self.assertIsNone(trace.result.data_fresh)
        self.assertEqual(trace.result.source_errors, ["provider timeout"])

    def test_explicit_partial_outcome_wins_over_empty_payload_inference(self) -> None:
        trace = AgentToolTrace(
            name="multi_source_tool",
            input={},
            summary="one source succeeded with no rows",
            output={"items": []},
            result=AgentToolOutcome(
                status="partial",
                data_fresh=True,
                source_errors=["secondary source unavailable"],
                payload={"items": []},
            ),
        )

        assert trace.result is not None
        self.assertEqual(trace.result.status, "partial")
        cards = build_agent_evidence_cards([trace])
        self.assertEqual(cards[0].metrics["partial_count"], 1)
        self.assertEqual(cards[1].status, "skipped")
        self.assertIn("不完整", cards[1].summary)

    def test_answerability_uses_outcome_instead_of_naked_empty_payload(self) -> None:
        empty = AgentToolTrace(
            name="limit_up_events",
            input={},
            summary="no rows",
            output={"events": [], "matched_count": 0},
        )
        failed = AgentToolTrace(
            name="limit_up_events",
            input={},
            summary="failed",
            status="error",
            error="provider timeout",
        )

        self.assertTrue(_has_usable_tool_facts({}, [empty]))
        self.assertFalse(_has_usable_tool_facts({"other_error": "x"}, [failed]))
        self.assertEqual(_tool_outcome_warnings([empty]), [])
        self.assertIn("查询失败", _tool_outcome_warnings([failed])[0])


if __name__ == "__main__":
    unittest.main()
