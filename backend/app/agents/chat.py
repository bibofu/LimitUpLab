"""Single public entry for production Agent chat."""

import os
from typing import Callable

from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentChatResponse, AgentRun, ChatSessionMessage, ChatSessionMemory, LimitUpEvent
from app.repositories import FirstBoardRepository
from app.services.llm_provider import LLMProvider, get_llm_provider


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: FirstBoardRepository | None = None,
    recent_runs: list[AgentRun] | None = None,
    conversation_messages: list[ChatSessionMessage] | None = None,
    session_memory: ChatSessionMemory | None = None,
    llm_provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
    answer_event_callback: Callable[[str, dict], None] | None = None,
    runner_mode: str | None = None,
) -> AgentChatResponse:
    """Execute the selected production runner for every chat request."""

    mode = (runner_mode or os.getenv("LIMITUPLAB_AGENT_RUNNER", "new")).strip().lower()
    if mode == "legacy":
        from app.agents.react_runtime.runtime import run
        # Legacy answer callbacks remain accepted for caller compatibility, but must
        # not publish text before the HTTP transport has committed Journal.finish.
        # Progress stays live; the returned response is the sole answer handoff.
        return run(
            request, tool_registry or AgentToolRegistry(
                events=events, first_board_repository=repository or FirstBoardRepository(),
            ), llm_provider or get_llm_provider(), conversation_messages, session_memory, progress_callback,
        )
    if mode != "new":
        raise ValueError("LIMITUPLAB_AGENT_RUNNER must be new or legacy")

    from app.agents.runner.adapter import answer_with_runner
    return answer_with_runner(
        request,
        events,
        repository=repository,
        conversation_messages=conversation_messages,
        session_memory=session_memory,
        llm_provider=llm_provider,
        progress_callback=progress_callback,
        tool_registry=tool_registry,
    )
