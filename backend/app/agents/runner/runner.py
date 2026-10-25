"""Request-scoped business nodes hosted by the application durable graph.

Small observations are lossless; large observations expose immutable receipts
for bounded reads. Registration and final delivery belong to business services.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, ToolMessage

from app.agents.compliance import review_answer
from app.agents.query_contract import current_query_reference_date
from app.agents.run_lifecycle import CURRENT_CONTROL
from app.agents.runner.gateway import BusinessToolGateway
from app.agents.runner.guardrails import (
    DEADLINE_SECONDS,
    MAX_MODEL_ROUNDS,
    MAX_TOOL_CALLS,
    REFUSAL,
    derived_status,
    requires_compliance_review,
)
from app.agents.runner.prompt import build_system_prompt
from app.agents.runner.result import RunnerResult
from app.agents.runner.tool_view import business_definitions, dump, model_result
from app.agents.result_paging import (
    PAGE_TOKEN_BUDGET, READ_RESULT_DEFINITION, READ_RESULT_TOOL, RESULT_READING_RULE,
    ReadModelResultRequest, ResultPageError, model_observation,
)
from app.services.agent_tracing import get_agent_tracer
from app.services.llm_provider import current_llm_usage


class RunnerState(TypedDict, total=False):
    schema_version: int
    run_id: str
    current_query: str
    conversation: dict
    remaining_seconds: float
    context_diagnostics: dict
    resolved_reference: dict
    memory_request: dict
    memory_action: dict
    memory_owner_revision: int
    memory_context_fingerprint: str
    memory_context_used: bool
    messages: list
    pending: list
    answer: str
    status: str
    stop_reason: str
    tool_calls: list[str]
    tool_statuses: list[str]
    tool_messages: list[dict[str, Any]]
    model_rounds: int
    tool_count: int
    error: str
    done: bool
    answer_repair: bool
    compliance_reviewed: bool


class AgentRunner:
    """Per-request model and tool dependencies, never a separate compiled graph."""

    def __init__(
        self,
        request: Any,
        registry: Any,
        provider: Any,
        history: list[Any] | None = None,
        *,
        deadline_seconds: float = DEADLINE_SECONDS,
        review_all_answers: bool = False,
        progress=None,
    ):
        self.request = request
        self.history = history or []
        self.provider = provider
        self.gateway = BusinessToolGateway(registry)
        self.definitions = [*business_definitions(self.gateway), READ_RESULT_DEFINITION]
        self.system_prompt = build_system_prompt(
            current_query_reference_date(),
            enabled_tools=set(self.gateway.structured),
        ) + RESULT_READING_RULE
        self.deadline = perf_counter() + deadline_seconds
        self.started = perf_counter()
        self.progress = progress
        self.review_all_answers = review_all_answers
        self.cache: dict[str, dict[str, Any]] = {}
        # One stable payload per request; the provider resends it every round.
        self.definition_chars = len(json.dumps(
            self.definitions, ensure_ascii=False, separators=(",", ":"),
        ))

    def agent(self, state: RunnerState) -> RunnerState:
        """Ask the model to either call tools or produce the final answer."""

        if state.get("done"):
            return state
        if _cancelled():
            return self._cancelled_stop()
        if state.get("model_rounds", 0) >= MAX_MODEL_ROUNDS or perf_counter() >= self.deadline:
            return self._budget_stop(state)
        if self.progress and state.get("model_rounds", 0) == 0:
            self.progress("planning", "正在理解问题并选择查询")

        def on_chunk(_chunk) -> None:
            if _cancelled() or perf_counter() >= self.deadline:
                raise InterruptedError("Run cancelled or deadline exceeded")

        try:
            repairing = state.get("answer_repair", False)
            self._record_usage_context(state, repairing)
            response = self.provider.generate_messages(
                state["messages"],
                [] if repairing else self.definitions,
                timeout_seconds=max(1, min(35, self.deadline - perf_counter())),
                max_tokens=4096,
                tool_choice="none" if repairing else "auto",
                on_chunk=on_chunk,
            )
        except Exception as error:  # noqa: BLE001 - runner boundary reports the failure
            if _cancelled():
                return self._cancelled_stop()
            if perf_counter() >= self.deadline:
                return self._budget_stop(state)
            from app.agents.runtime_errors import RecoverableRunError
            raise RecoverableRunError("模型调用暂时失败，可恢复原任务。") from error
        messages = [*state["messages"], response]
        rounds = state.get("model_rounds", 0) + 1
        tool_calls = getattr(response, "tool_calls", None) or []
        finish_reason = (getattr(response, "response_metadata", None) or {}).get("finish_reason")
        if finish_reason == "length":
            if repairing or rounds >= MAX_MODEL_ROUNDS:
                return {**self._incomplete_stop(state), "model_rounds": rounds}
            if self.progress:
                self.progress("answering", "回答达到长度上限，正在整理完整的精简回答")
            # Discard the interrupted draft, including any incomplete tool plan.
            # Reuse the same evidence and allow one text-only rewrite within the
            # original request budget; never append a continuation to half a draft.
            return {
                "messages": [*state["messages"], HumanMessage(content=(
                    "上一轮输出达到长度上限，尚未形成完整回答。请基于已有工具结果，"
                    "重新给出一份紧凑、完整且以完整句子结束的最终回答。不得再查询工具。"
                    "优先保留用户明确要求的全部记录、数字、口径、来源和数据缺口；"
                    "用表格合并重复解释，删去重复背景。没有取得证据的部分明确说明未完成，"
                    "不得用概括代替用户要求的完整名单。不要续写或引用上一轮未完成的草稿。"
                ))],
                "pending": [],
                "model_rounds": rounds,
                "answer_repair": True,
            }
        if finish_reason == "content_filter" or (repairing and tool_calls):
            return {**self._incomplete_stop(state), "model_rounds": rounds}
        if tool_calls:
            return {"messages": messages, "pending": tool_calls, "model_rounds": rounds}
        content = response.content.strip() if isinstance(response.content, str) else ""
        if repairing and not content:
            return {**self._incomplete_stop(state), "model_rounds": rounds}
        answer, refused, reviewed = content, False, False
        if content and (self.review_all_answers or requires_compliance_review(content)):
            if _cancelled():
                return self._cancelled_stop()
            remaining = self.deadline - perf_counter()
            if rounds >= MAX_MODEL_ROUNDS or remaining < 1:
                return self._compliance_stop(rounds, "ReviewBudgetExhausted")
            if self.progress:
                self.progress("checking", "正在核验回答是否包含交易指令")
            rounds += 1
            tracker = current_llm_usage()
            if tracker is not None:
                tracker.set_context(
                    stage="compliance_review",
                    round=rounds,
                    answer_chars=len(content),
                )
            try:
                review = review_answer(
                    self.provider, user_message=self.request.message, answer=content,
                    timeout_seconds=min(10, remaining),
                )
            except Exception as error:  # noqa: BLE001 - uncertain output is never published
                if _cancelled():
                    return self._cancelled_stop()
                return self._compliance_stop(rounds, type(error).__name__)
            if _cancelled():
                return self._cancelled_stop()
            if perf_counter() >= self.deadline:
                return self._compliance_stop(rounds, "ReviewDeadlineExceeded")
            reviewed = True
            if review.decision == "reject":
                answer, refused = REFUSAL, True
        return {
            "messages": messages,
            "answer": answer,
            "status": "refuse" if refused else derived_status(answer, state.get("tool_statuses", [])),
            "stop_reason": "compliance_refused" if refused else (
                "answered_after_length_recovery" if repairing else "answered"
            ),
            "model_rounds": rounds,
            "compliance_reviewed": reviewed,
            "done": True,
        }

    def _record_usage_context(self, state: RunnerState, repairing: bool) -> None:
        """Attach the context cost of this round to the next provider call."""

        tracker = current_llm_usage()
        if tracker is None:
            return
        tracker.set_context(
            stage="length_repair" if repairing else "agent",
            round=state.get("model_rounds", 0) + 1,
            tool_count=0 if repairing else len(self.definitions),
            tool_definition_chars=0 if repairing else self.definition_chars,
            message_chars=sum(
                _message_chars(item) for item in state.get("messages", [])
            ),
            tool_results=len(state.get("tool_messages", [])),
            pending_tools=len(state.get("pending", [])),
            **state.get("context_diagnostics", {}),
        )

    def tools(self, state: RunnerState) -> RunnerState:
        """Execute every pending call and append one observation per call."""

        if _cancelled():
            return self._cancelled_stop()
        messages = list(state["messages"])
        tool_calls = list(state.get("tool_calls", []))
        tool_statuses = list(state.get("tool_statuses", []))
        tool_messages = list(state.get("tool_messages", []))
        tool_count = state.get("tool_count", 0)
        if self.progress:
            self.progress("tools", f"正在执行 {len(state['pending'])} 项数据查询")
        for call in state["pending"]:
            if tool_count >= MAX_TOOL_CALLS:
                value = {"status": "error", "error": "Tool execution budget exhausted"}
            else:
                tool_count += 1
                value = self.execute(call)
            tool_calls.append(call["name"])
            tool_statuses.append(value.get("status", "error"))
            tool_messages.append({**value, "name": call["name"]})
            try:
                model_value = model_observation(value, call["id"])
            except ResultPageError:
                # Do not claim a partial or cut field is the full observation.
                return {"answer": "工具结果的字段说明超出上下文预算，请缩小查询范围；完整证据仍保留。",
                        "status": "partial", "stop_reason": "context_budget", "done": True,
                        "tool_calls": tool_calls, "tool_statuses": tool_statuses,
                        "tool_messages": tool_messages, "tool_count": tool_count}
            messages.append(ToolMessage(
                content=dump(model_value),
                tool_call_id=call["id"],
                name=call["name"],
            ))
        return {
            "messages": messages,
            "pending": [],
            "tool_calls": tool_calls,
            "tool_statuses": tool_statuses,
            "tool_messages": tool_messages,
            "tool_count": tool_count,
        }

    def execute(self, call: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute one call, returning a model-readable observation."""

        with get_agent_tracer().tool(
            name=call.get("name", "unknown"), arguments=call.get("args"),
        ) as span:
            from app.repositories.tool_result_repository import ToolResultRepository
            value = ToolResultRepository(CURRENT_CONTROL.get()).execute(call, lambda: self._execute(call))
            span.finish(status=value.get("status", "error"), output=value)
            return value

    # Validate and execute one call; validation and provider failures stay observations.
    def _execute(self, call: dict[str, Any]) -> dict[str, Any]:
        try:
            if call["name"] == READ_RESULT_TOOL:
                from app.services.agent_result_reader import read_owned_result
                control = CURRENT_CONTROL.get()
                if control is None:
                    raise ValueError("Saved result reads require an owned durable run")
                request = ReadModelResultRequest.model_validate(call["args"])
                return read_owned_result(control.lifecycle.database_url, owner_id=control.row["owner_id"],
                                         run_id=control.row["run_id"], request=request, max_tokens=PAGE_TOKEN_BUDGET)
            arguments = self.gateway.validate(call)
            signature = dump([call["name"], sorted(arguments.items())])
            if signature in self.cache:
                return self.cache[signature]
            result, payload, status = self.gateway.execute(call["name"], arguments)
            value = model_result(call["name"], result, payload, status, arguments=arguments,
                                 allowed_arguments=self.gateway.structured[call["name"]].args_schema.model_fields)
            self.cache[signature] = value
            return value
        except Exception as error:  # noqa: BLE001 - validation/provider failures are observations
            return {
                "status": "error",
                "error": str(error),
                "error_type": type(error).__name__,
            }

    def _cancelled_stop(self) -> RunnerState:
        return {
            "answer": "任务已取消，未继续执行新的数据查询。",
            "status": "cancelled",
            "stop_reason": "cancelled",
            "done": True,
        }

    def _budget_stop(self, state: RunnerState) -> RunnerState:
        if state.get("answer_repair"):
            return self._incomplete_stop(state)
        summaries = [
            item.get("summary")
            for item in state.get("tool_messages", [])
            if item.get("summary")
        ]
        if summaries:
            answer = "查询预算已用完。已取得以下事实：\n" + "\n".join(
                f"- {summary}" for summary in summaries
            )
            status = "partial"
        else:
            answer = "查询预算已用完，未取得可核验结果。"
            status = "error"
        return {
            "answer": answer,
            "status": status,
            "stop_reason": "budget_exhausted",
            "done": True,
        }

    def _incomplete_stop(self, state: RunnerState) -> RunnerState:
        """Publish only evidence summaries when a complete answer is unavailable."""

        summaries = [item["summary"] for item in state.get("tool_messages", []) if item.get("summary")]
        answer = "回答未完整生成，本次未能完成全部问题。"
        if summaries:
            answer += "以下仅为已取得的事实摘要，不能视为完整回答：\n" + "\n".join(
                f"- {summary}" for summary in summaries
            )
        else:
            answer += "尚无可核验的工具结果。"
        return {
            "answer": answer,
            "status": "partial" if summaries else "error",
            "stop_reason": "output_incomplete",
            "done": True,
        }

    def _compliance_stop(self, rounds: int, error: str) -> RunnerState:
        return {
            "answer": "回答合规检查未完成，本次结果未发布。",
            "status": "error", "stop_reason": "compliance_error",
            "model_rounds": rounds, "error": error, "done": True,
        }

    def run(self) -> RunnerResult:
        """Execute only through the application-owned durable graph."""

        from app.agents.runner.durable_runtime import get_runtime
        runtime = get_runtime()
        control = CURRENT_CONTROL.get()
        if control is None or control.row.get("runtime_version") != "durable-v1":
            raise RuntimeError("Agent execution requires a durable run and execution owner")
        if runtime is None:
            from app.agents.runtime_errors import RecoverableRunError
            raise RecoverableRunError("持久化运行时暂不可用，请恢复原任务。")
        return runtime.run_sync(self, control)


def _message_chars(message: Any) -> int:
    """Approximate one resent message's text size, including tool-call arguments."""

    content = getattr(message, "content", "")
    characters = len(content) if isinstance(content, str) else len(dump(content))
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        characters += len(dump(calls))
    return characters


def _cancelled() -> bool:
    control = CURRENT_CONTROL.get()
    return control is not None and control.cancelled()
