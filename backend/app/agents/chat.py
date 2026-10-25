"""Single public entry for production Agent chat."""

from typing import Callable

from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentChatResponse, ChatSessionMessage, LimitUpEvent
from app.repositories import FirstBoardRepository
from app.services.llm_provider import LLMProvider


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: FirstBoardRepository | None = None,
    conversation_messages: list[ChatSessionMessage] | None = None,
    llm_provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
    answer_event_callback: Callable[[str, dict], None] | None = None,
) -> AgentChatResponse:
    """Execute the durable production runner for an owned chat request."""

    from app.agents.runner.adapter import answer_with_runner

    return answer_with_runner(
        request,
        events,
        repository=repository,
        conversation_messages=conversation_messages,
        llm_provider=llm_provider,
        progress_callback=progress_callback,
        tool_registry=tool_registry,
    )
