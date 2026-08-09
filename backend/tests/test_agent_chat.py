import unittest
from datetime import date, datetime, time, timezone

from app.agents.chat import answer_first_board_chat
from app.models import AgentChatRequest, AgentRun, LimitUpEvent
from app.services.sample_data import SAMPLE_EVENTS


class AgentChatTest(unittest.TestCase):
    def _make_event(
        self,
        symbol: str,
        name: str,
        industry: str,
        concept: str,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=date(2026, 8, 7),
            first_limit_time=time(10, 0),
            last_limit_time=time(10, 0),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=250_000_000,
            turnover_rate=7.5,
            industry=industry,
            concept=concept,
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def test_greeting_does_not_route_to_market_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(session_id="s1", message="\u4f60\u597d"),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "greeting")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("LimitUpLab", response.answer)

    def test_market_schedule_does_not_route_to_rating_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="A\u80a1\u51e0\u70b9\u5f00\u76d8",
                intent_hint="market_schedule",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "market_schedule")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("09:30", response.answer)
        self.assertIn("15:00", response.answer)

    def test_market_sentiment_question_uses_market_context_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u6700\u8fd1A\u80a1\u5e02\u573a\u7684\u60c5\u7eea\u600e\u4e48\u6837",
                intent_hint="market_context",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "market_context")
        self.assertIn("market_summary", response.tool_calls)
        self.assertIn("2026-05-15", response.answer)

    def test_today_summary_uses_first_board_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u603b\u7ed3\u4e00\u4e0b\u4eca\u5929\u9996\u677f",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertIn("2026-05-15", response.answer)
        self.assertTrue(response.warnings)

    def test_shorthand_date_can_select_historical_first_board_data(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="5.15\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("2026-05-15", response.answer)

    def test_dated_first_board_data_question_uses_ratings_not_general_llm(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="5.15\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertEqual(response.tool_calls, ["first_board_ratings"])

    def test_dated_first_board_topic_question_filters_candidate_pool(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.7\u65e5\u9996\u677f\u7684\u80a1\u7968\uff0c\u662f\u533b\u836f\u76f8\u5173\u7684\u6709\u54ea\u4e9b",
            ),
            events=events,
        )

        self.assertEqual(response.intent, "first_board_filter")
        self.assertEqual(response.tool_calls, ["first_board_ratings", "first_board_filter"])
        self.assertEqual(response.tool_results[0].name, "agent_plan")
        self.assertEqual(response.tool_results[0].input["intent"], "first_board_filter")
        self.assertIn("002001", response.answer)
        self.assertNotIn("002002", response.answer)

    def test_agent_plan_trace_is_returned_for_tool_answers(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u603b\u7ed3\u4e00\u4e0b\u4eca\u5929\u9996\u677f",
            ),
            events=SAMPLE_EVENTS,
        )

        plan_trace = response.tool_results[0]
        self.assertEqual(plan_trace.name, "agent_plan")
        self.assertEqual(plan_trace.input["intent"], "today_summary")
        self.assertEqual(
            plan_trace.input["tool_steps"][0]["name"],
            "first_board_ratings",
        )

    def test_filter_and_similar_question_is_planned_as_multi_tool_flow(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message=(
                    "8.7\u65e5\u9996\u677f\u91cc\u533b\u836f\u76f8\u5173"
                    "\u8bc4\u5206\u6700\u9ad8\u7684\u662f\u8c01\uff0c"
                    "\u6709\u6ca1\u6709\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b"
                ),
            ),
            events=events,
        )

        self.assertEqual(response.intent, "first_board_filter_similar")
        self.assertEqual(
            response.tool_calls,
            ["first_board_ratings", "first_board_filter", "first_board_similar_cases"],
        )
        self.assertEqual(response.tool_results[0].name, "agent_plan")
        self.assertEqual(
            [step["name"] for step in response.tool_results[0].input["tool_steps"]],
            ["first_board_ratings", "first_board_filter", "first_board_similar_cases"],
        )
        self.assertIn("002001", response.answer)
        self.assertIn("\u7279\u5f81\u7f13\u5b58", response.answer)

    def test_missing_shorthand_date_reports_data_availability(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.6\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "data_availability")
        self.assertIn("limit_up_event_dates", response.tool_calls)
        self.assertIn("2026-08-06", response.answer)

    def test_symbol_question_uses_context_symbol(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3a\u4ec0\u4e48\u8bc4\u5206\u9ad8",
                trade_date=date(2026, 5, 15),
                symbol="301489",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "rating_explain")
        self.assertIn("301489", response.answer)
        self.assertIn("first_board_ratings", response.tool_calls)

    def test_unknown_symbol_is_not_hallucinated(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3a\u4ec0\u4e48 000001 \u8bc4\u5206\u9ad8",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "symbol_not_found")
        self.assertIn("000001", response.answer)

    def test_follow_up_can_use_recent_run_context_symbol(self) -> None:
        previous_run = AgentRun(
            run_id="run_previous",
            session_id="s1",
            run_type="agent_chat",
            status="success",
            intent="rating_explain",
            tool_calls=["first_board_ratings"],
            input_json={"symbol": "301489", "trade_date": "2026-05-15"},
            output_json={"answer": "previous"},
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3b\u8981\u98ce\u9669\u662f\u4ec0\u4e48",
                intent_hint="risk_summary",
            ),
            events=SAMPLE_EVENTS,
            recent_runs=[previous_run],
        )

        self.assertEqual(response.intent, "risk_summary")
        self.assertIn("301489", response.answer)

    def test_llm_explanation_intent_uses_explanation_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u8be6\u7ec6\u89e3\u91ca\u4e00\u4e0b",
                intent_hint="llm_explanation",
                trade_date=date(2026, 5, 15),
                symbol="301489",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "llm_explanation")
        self.assertTrue(
            {"llm_explanation", "template_explanation"} & set(response.tool_calls)
        )
        self.assertIn("301489", response.answer)

    def test_output_avoids_investment_advice_terms(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="301489 \u6709\u4ec0\u4e48\u98ce\u9669",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
        )
        rendered = response.model_dump_json()

        forbidden_terms = [
            "\u4e70\u5165",
            "\u5356\u51fa",
            "\u4ed3\u4f4d",
            "\u76ee\u6807\u4ef7",
            "\u6536\u76ca\u627f\u8bfa",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, rendered)


if __name__ == "__main__":
    unittest.main()
