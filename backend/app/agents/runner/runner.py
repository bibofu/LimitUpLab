"""Minimal LangGraph tool-use loop for the Phase 2 shadow run.

The production graph remains ``react_runtime``. This runner keeps LangGraph but
removes evidence IDs, finish/read/compute/update control tools and the input
compiler; the model receives complete, self-describing tool results directly.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, TypedDict

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.lifecycle import CURRENT_CONTROL
from app.agents.react_runtime.tools import ToolGateway
from app.agents.runner.guardrails import (
    DEADLINE_SECONDS,
    MAX_MODEL_ROUNDS,
    MAX_TOOL_CALLS,
    compliance_guard,
    derived_status,
)
from app.agents.runner.prompt import build_messages
from app.agents.runner.result import RunnerResult
from app.agents.runner.tool_view import business_definitions, dump, model_result


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
        progress=None,
    ):
        self.request = request
        self.provider = provider
        self.gateway = ToolGateway(registry, EvidenceStore())
        self.definitions = business_definitions(self.gateway)
        self.messages = build_messages(request, history, current_query_reference_date())
        self.deadline = perf_counter() + deadline_seconds
        self.started = perf_counter()
        self.progress = progress
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
            response = self.provider.generate_messages(
                state["messages"],
                self.definitions,
                timeout_seconds=max(1, min(35, self.deadline - perf_counter())),
                max_tokens=4096,
                tool_choice="auto",
                on_chunk=on_chunk,
            )
        except Exception as error:  # noqa: BLE001 - runner boundary reports the failure
            if _cancelled():
                return self._cancelled_stop()
            if perf_counter() >= self.deadline:
                return self._budget_stop(state)
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
        if tool_calls:
            return {"messages": messages, "pending": tool_calls, "model_rounds": rounds}
        content = response.content.strip() if isinstance(response.content, str) else ""
        answer, refused = compliance_guard(content)
        return {
            "messages": messages,
            "answer": answer,
            "status": "refuse" if refused else derived_status(answer, state.get("tool_statuses", [])),
            "stop_reason": "compliance_refused" if refused else "answered",
            "model_rounds": rounds,
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
            tool_messages.append({"name": call["name"], **value})
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
