import unittest

from app.models import AgentToolOutcome, AgentToolTrace, build_agent_evidence_cards


class AgentToolOutcomeTest(unittest.TestCase):
    # Regression scenario: successful empty payload is not a tool error.
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

    # Regression scenario: source population must not hide an empty query match.
    def test_matched_count_wins_over_positive_source_population(self) -> None:
        trace = AgentToolTrace(
            name="dragon_tiger_list",
            input={"query": "605058,605011,605088"},
            summary="龙虎榜命中 0 条",
            output={
                "stock_count": 36,
                "matched_count": 0,
                "items": [],
            },
        )

        assert trace.result is not None
        self.assertEqual(trace.result.status, "empty")

    # Count-only tools may intentionally omit rows while still reporting matches.
    def test_positive_matched_count_keeps_count_only_payload_non_empty(self) -> None:
        trace = AgentToolTrace(
            name="limit_up_events",
            input={"result_mode": "count"},
            summary="涨停查询命中 12 只",
            output={"matched_count": 12, "returned_count": 0, "items": []},
        )

        assert trace.result is not None
        self.assertEqual(trace.result.status, "ok")

    # Regression scenario: partial payload preserves source errors.
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

    # Regression scenario: legacy error trace is normalized to error outcome.
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

    # Regression scenario: explicit partial outcome wins over empty payload inference.
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



if __name__ == "__main__":
    unittest.main()
