"""Production adapter from the lightweight runner to the public chat response."""

from __future__ import annotations

import os
from typing import Any, Callable

from app.agents.runner.runner import AgentRunner
from app.agents.tools import AgentToolRegistry
from app.models import (
    AgentChatPerformance,
    AgentChatRequest,
    AgentChatResponse,
    AgentToolOutcome,
    AgentToolTrace,
)
from app.repositories import FirstBoardRepository
from app.services.llm_provider import get_llm_provider

_STATUS_MAP = {
    "answered": "complete",
    "partial": "partial",
    "empty": "empty",
    "clarify": "clarify",
    "refuse": "refuse",
    "error": "error",
    "cancelled": "cancelled",
}


def _response(
    request: AgentChatRequest,
    answer: str,
    status: str,
    stop_reason: str,
    *,
    tool_calls: list[str] | None = None,
    tool_results: list[AgentToolTrace] | None = None,
    duration_ms: int = 0,
) -> AgentChatResponse:
    return AgentChatResponse(
        session_id=request.session_id,
        intent="react_research",
        answer=answer,
        task_status=_STATUS_MAP.get(status, "error"),
        stop_reason=stop_reason,
        tool_calls=tool_calls or [],
        tool_results=tool_results or [],
        performance=AgentChatPerformance(total_duration_ms=duration_ms),
        generated_by="agent-runner-v1",
    )


def _traces(result) -> list[AgentToolTrace]:
    traces = []
    for item in result.tool_messages:
        status = item.get("status", "error")
        traces.append(AgentToolTrace(
            name=item["name"],
            summary=item.get("summary") or item.get("error") or item["name"],
            status="error" if status == "error" else "success",
            output=item,
            error=item.get("error"),
            result=AgentToolOutcome(status=status, payload=item),
        ))
    return traces


def answer_with_runner(
    request: AgentChatRequest,
    events: list[Any],
    *,
    repository: FirstBoardRepository | None = None,
    conversation_messages: list[Any] | None = None,
    llm_provider: Any | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
) -> AgentChatResponse:
    """Map the single durable graph's result to the public response contract."""

    provider = llm_provider or get_llm_provider()
    repository = repository or FirstBoardRepository()
    registry = tool_registry or AgentToolRegistry(
        events=events,
        first_board_repository=repository,
    )

    if progress_callback:
        progress_callback("preparing", "正在检查输入安全与研究边界")

    compliance_mode = os.getenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic").strip().lower()
    result = AgentRunner(
        request,
        registry,
        provider,
        conversation_messages,
        progress=progress_callback,
        review_all_answers=compliance_mode != "deterministic",
    ).run()

    traces = _traces(result)
    response = _response(
        request,
        result.answer,
        result.status,
        result.stop_reason,
        tool_calls=list(result.tool_calls),
        tool_results=traces,
        duration_ms=result.duration_ms,
    )
    return response.model_copy(update={
        "memory_action": result.memory_action,
        "memory_owner_revision": result.memory_owner_revision,
        "memory_context_fingerprint": result.memory_context_fingerprint,
        "memory_dependencies": result.memory_dependencies,
        "memory_context_used": result.memory_context_used,
    })
