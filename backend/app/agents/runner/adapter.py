"""Production adapter from the lightweight runner to the public chat response."""

from __future__ import annotations

import os
from typing import Any, Callable

from app.agents.runner.guardrails import REFUSAL
from app.agents.runner.runner import AgentRunner
from app.agents.runner.security import review_input_security
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
    session_memory: Any | None = None,
    llm_provider: Any | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
) -> AgentChatResponse:
    """Run the Phase 2/3 loop and map its result to the public response contract.

    ``session_memory`` is accepted for caller compatibility; recent conversation
    text is already supplied through ``conversation_messages``. Input review
    checks security and trading-advice requests before business queries; there
    is no task/output/date compiler on this path.
    """

    provider = llm_provider or get_llm_provider()
    repository = repository or FirstBoardRepository()
    registry = tool_registry or AgentToolRegistry(
        events=events,
        first_board_repository=repository,
    )

    if progress_callback:
        progress_callback("preparing", "正在检查输入安全与研究边界")
    from app.agents.run_lifecycle import CURRENT_CONTROL
    from app.agents.runner.durable_runtime import get_runtime
    # Durable input review is a checkpointed node. Its result survives retries.
    control = CURRENT_CONTROL.get()
    durable = control is not None and control.row.get("runtime_version") == "durable-v1"
    if durable and get_runtime() is None:
        from app.agents.runtime_errors import RecoverableRunError
        raise RecoverableRunError("持久化运行时暂不可用，请恢复原任务。")
    try:
        assessment = None if durable else review_input_security(
            provider,
            message=request.message,
            timeout_seconds=10,
        )
    except Exception:  # noqa: BLE001 - input review failure fails closed
        return _response(
            request,
            "输入安全检查未完成，本次未执行数据查询，请稍后重试。",
            "error",
            "input_policy_error",
        )
    if assessment is not None and assessment.decision == "refuse":
        trading_advice = "trading_advice" in assessment.signals
        return _response(
            request,
            REFUSAL if trading_advice else "我可以协助查询有来源的股票研究事实，不能执行绕过系统边界的指令。",
            "refuse",
            "input_trading_advice" if trading_advice else "input_policy",
        )

    compliance_mode = os.getenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic").strip().lower()
    result = AgentRunner(
        request,
        registry,
        provider,
        conversation_messages,
        progress=progress_callback,
        memory=session_memory,
        review_all_answers=compliance_mode != "deterministic",
    ).run()

    traces = _traces(result)
    return _response(
        request,
        result.answer,
        result.status,
        result.stop_reason,
        tool_calls=list(result.tool_calls),
        tool_results=traces,
        duration_ms=result.duration_ms,
    )
