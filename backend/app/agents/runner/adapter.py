"""Production adapter from the lightweight runner to the public chat response."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Callable

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.compliance import review_answer
from app.agents.runner.guardrails import REFUSAL
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
from app.services.prompt_security import review_input

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
    text is already supplied through ``conversation_messages``.
    """

    provider = llm_provider or get_llm_provider()
    repository = repository or FirstBoardRepository()
    registry = tool_registry or AgentToolRegistry(
        events=events,
        first_board_repository=repository,
    )

    if progress_callback:
        progress_callback("preparing", "正在检查请求与研究范围")
    try:
        assessment = review_input(
            provider,
            message=request.message,
            timeout_seconds=15,
            anchor_date=current_query_reference_date(),
            task_context={},
            page_defaults={"trade_date": request.trade_date, "symbol": request.symbol},
        )
    except Exception:  # noqa: BLE001 - input review failure fails closed
        return _response(
            request,
            "输入检查与任务解析未完成，本次未执行数据查询，请稍后重试。",
            "error",
            "input_policy_error",
        )
    if assessment.detected:
        return _response(
            request,
            "我可以协助查询有来源的股票研究事实，不能执行绕过系统边界的指令。",
            "refuse",
            "input_policy",
        )

    result = AgentRunner(
        request,
        registry,
        provider,
        conversation_messages,
        progress=progress_callback,
    ).run()

    compliance_mode = os.getenv("LIMITUPLAB_AGENT_COMPLIANCE", "llm").strip().lower()
    if result.answer and result.status != "refuse" and compliance_mode != "deterministic":
        if progress_callback:
            progress_callback("checking", "正在校验回答")
        try:
            review = review_answer(
                provider,
                user_message=request.message,
                answer=result.answer,
                timeout_seconds=20,
            )
        except Exception:  # noqa: BLE001 - compliance failure fails closed
            return _response(
                request,
                "回答合规检查未完成，本次结果未发布。",
                "error",
                "compliance_error",
            )
        if review.decision != "allow":
            result = replace(
                result,
                answer=REFUSAL,
                status="refuse",
                stop_reason="compliance_refused",
            )

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
