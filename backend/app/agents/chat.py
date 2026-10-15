"""Single public entry for production ReAct chat."""

from typing import Callable

from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentChatResponse, AgentRun, ChatSessionMessage, ChatSessionMemory, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, get_llm_provider


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository | None = None,
    recent_runs: list[AgentRun] | None = None,
    conversation_messages: list[ChatSessionMessage] | None = None,
    session_memory: ChatSessionMemory | None = None,
    llm_provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
    answer_event_callback: Callable[[str, dict], None] | None = None,
) -> AgentChatResponse:
    """Execute the single ReAct runtime for every chat request."""
    from app.agents.react_runtime.runtime import run
    # Legacy answer callbacks remain accepted for caller compatibility, but must
    # not publish text before the HTTP transport has committed Journal.finish.
    # Progress stays live; the returned response is the sole answer handoff.
    return run(
        request, tool_registry or AgentToolRegistry(
            events=events, first_board_repository=repository or SQLiteFirstBoardRepository(),
        ), llm_provider or get_llm_provider(), conversation_messages, session_memory, progress_callback,
    )
