"""Minimal production LangGraph tool-use loop.

It has no evidence IDs, delivery control tools or task/date compiler; the model
receives complete, self-describing tool results directly.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

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
from app.agents.runner.prompt import build_messages
from app.agents.runner.result import RunnerResult
from app.agents.runner.tool_view import business_definitions, dump, model_result
from app.services.agent_tracing import get_agent_tracer


class RunnerState(TypedDict, total=False):
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
    """One request-scoped LangGraph loop over business tools only."""

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
        memory=None,
    ):
        self.request = request
        self.provider = provider
        self.gateway = BusinessToolGateway(registry)
        self.definitions = business_definitions(self.gateway)
        self.messages = build_messages(
            request, history, current_query_reference_date(), memory=memory
        )
        self.deadline = perf_counter() + deadline_seconds
        self.started = perf_counter()
        self.progress = progress
        self.review_all_answers = review_all_answers
        self.cache: dict[str, dict[str, Any]] = {}

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
            if state.get("answer_repair"):
                return {**self._incomplete_stop(state), "error": type(error).__name__}
            return {
                "answer": "本次研究执行失败，未取得可核验结果。",
                "status": "error",
                "stop_reason": "provider_error",
                "error": type(error).__name__,
                "done": True,
            }
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
            messages.append(ToolMessage(
                content=dump(value), tool_call_id=call["id"], name=call["name"],
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
            value = self._execute(call)
            span.finish(status=value.get("status", "error"), output=value)
            return value

    # Validate and execute one call; validation and provider failures stay observations.
    def _execute(self, call: dict[str, Any]) -> dict[str, Any]:
        try:
            arguments = self.gateway.validate(call)
            signature = dump([call["name"], sorted(arguments.items())])
            if signature in self.cache:
                return self.cache[signature]
            result, payload, status = self.gateway.execute(call["name"], arguments)
            value = model_result(call["name"], result, payload, status)
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
        """Execute one bounded shadow run."""

        initial: RunnerState = {
            "messages": self.messages,
            "pending": [],
            "tool_calls": [],
            "tool_statuses": [],
            "tool_messages": [],
            "model_rounds": 0,
            "tool_count": 0,
            "done": False,
        }
        try:
            final = GRAPH.invoke(
                initial,
                config={"configurable": {"runner": self}, "recursion_limit": 40},
            )
        except Exception as error:  # noqa: BLE001 - graph boundary
            final = {
                **initial,
                "answer": "本次研究执行失败，未取得可核验结果。",
                "status": "error",
                "stop_reason": "runner_error",
                "error": type(error).__name__,
                "done": True,
            }
        return RunnerResult(
            answer=final.get("answer", ""),
            status=final.get("status", "error"),
            stop_reason=final.get("stop_reason", "unknown"),
            tool_calls=final.get("tool_calls", []),
            tool_messages=final.get("tool_messages", []),
            model_rounds=final.get("model_rounds", 0),
            tool_count=final.get("tool_count", 0),
            duration_ms=round((perf_counter() - self.started) * 1000),
            error=final.get("error"),
            compliance_reviewed=final.get("compliance_reviewed", False),
        )


def _cancelled() -> bool:
    control = CURRENT_CONTROL.get()
    return control is not None and control.cancelled()


def _node(name: str):
    def call(state, config):
        return getattr(config["configurable"]["runner"], name)(state)

    return call


def _graph():
    graph = StateGraph(RunnerState)
    graph.add_node("agent", _node("agent"))
    graph.add_node("tools", _node("tools"))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        lambda state: END if state.get("done") else "tools" if state.get("pending") else "agent",
    )
    graph.add_edge("tools", "agent")
    return graph.compile()


GRAPH = _graph()
