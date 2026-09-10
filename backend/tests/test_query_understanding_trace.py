"""Production chat responses expose the deterministic Query Understanding view."""

from datetime import date

from app.agents.chat import answer_first_board_chat
from app.agents.eval_runner import OfflineEvalLLMProvider
from app.agents.query_contract import query_reference_date_override
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS


def test_chat_response_appends_anchored_query_understanding_trace() -> None:
    with query_reference_date_override(date(2026, 9, 10)):
        response = answer_first_board_chat(
            request=AgentChatRequest(
                session_id="query-trace",
                message="今天创业板涨停股有哪些？",
            ),
            events=SAMPLE_EVENTS,
            repository=object(),  # type: ignore[arg-type]
            llm_provider=OfflineEvalLLMProvider(),
        )

    traces = [
        trace for trace in response.tool_results if trace.name == "query_understanding"
    ]
    assert len(traces) == 1
    assert traces[0].output["reference_date"] == "2026-09-10"
    assert traces[0].output["trade_date"] == "2026-09-10"
    assert traces[0].output["market"] == "chinext"
