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
    MAX_META_CALLS,
    MAX_MODEL_ROUNDS,
    MAX_SEARCH_CALLS,
    MAX_TOOL_CALLS,
    REFUSAL,
    derived_status,
    requires_compliance_review,
)
from app.agents.tool_profiles import ToolProfile, resolve_tool_profile
from app.agents.progressive_tools import (
    SEARCH_TOOLS_DEFINITION,
    SEARCH_TOOLS_TOOL,
    SearchToolsRequest,
    resolve_max_active_tools,
    resolve_progressive_tools_mode,
    search_tool_catalog,
)
from app.agents.runner.answer_delivery import AnswerDelivery
from app.agents.runner.prompt import build_system_prompt
from app.agents.runner.result import RunnerResult
from app.agents.runner.tool_view import business_definitions, dump, model_result
from app.agents.skills import resolve_agent_skill
from app.agents.result_paging import (
    PAGE_TOKEN_BUDGET, READ_RESULT_DEFINITION, READ_RESULT_TOOL,
    ReadModelResultRequest, ResultPageError, model_observation,
)
from app.services.agent_tracing import get_agent_tracer
from app.services.llm_provider import current_llm_usage


class RunnerState(TypedDict, total=False):
    schema_version: int
    run_id: str
    current_query: str
    reference_date: str
    pause_next_node: str
    pause_revision: int
    conversation: dict
    remaining_seconds: float
    context_diagnostics: dict
    reviewed: bool
    memory_request: dict
    memory_action: dict
    memory_owner_revision: int
    memory_context_fingerprint: str
    memory_dependencies: list[dict[str, Any]]
    display_policies: list[dict[str, Any]]
    memory_context_used: bool
    messages: list
    pending: list
    answer: str
    status: str
    stop_reason: str
    tool_calls: list[str]
    tool_statuses: list[str]
    tool_messages: list[dict[str, Any]]
    discovered_tools: list[str]
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
        tool_profile: ToolProfile | None = None,
        answer_revision_base: int = 0,
        progressive_mode: str | None = None,
        max_active_tools: int | None = None,
    ):
        self.request = request
        self.history = history or []
        self.provider = provider
        self.gateway = BusinessToolGateway(registry)
        self.progressive_mode = resolve_progressive_tools_mode(progressive_mode)
        self.max_active_tools = (
            resolve_max_active_tools(str(max_active_tools))
            if max_active_tools is not None else resolve_max_active_tools()
        )
        profile = tool_profile or resolve_tool_profile(request)
        enabled = set(getattr(registry, "enabled_tool_names", profile.tools))
        selected = profile.tools & enabled
        if not selected:
            selected = enabled
            profile = ToolProfile("full", frozenset(selected))
        self.tool_profile_name = profile.name
        self.searchable_tool_names = frozenset(enabled)
        # Full mode starts from the meta-tools only; hybrid keeps the
        # deterministic profile and discovers the rest on demand.
        self.initial_tool_names = (
            frozenset() if self.progressive_mode == "full" else frozenset(selected)
        )
        self.initial_business_order = tuple(
            name for name in self.gateway.structured
            if name in self.initial_tool_names
        )
        self.tool_names = self.initial_tool_names
        self._active_discovered: list[str] = []
        self.definitions = self._build_definitions(())
        self.freeze_reference_date(current_query_reference_date())
        self.deadline = perf_counter() + deadline_seconds
        self.started = perf_counter()
        self.progress = progress
        self.review_all_answers = review_all_answers
        self.cache: dict[str, dict[str, Any]] = {}
        # Streaming is attached by the durable runtime; direct node tests stay silent.
        self.delivery = AnswerDelivery(None, revision_base=answer_revision_base)
        # One stable payload per request; the provider resends it every round.
        self.definition_chars = len(json.dumps(
            self.definitions, ensure_ascii=False, separators=(",", ":"),
        ))

    def _build_definitions(self, discovered: tuple[str, ...]) -> list[dict[str, Any]]:
        """Keep an ordered, cache-friendly active set instead of the full catalog."""

        active = [name for name in self.initial_business_order if name in self.gateway.structured]
        for name in discovered:
            if (
                name in self.searchable_tool_names
                and name in self.gateway.structured
                and name not in active
            ):
                active.append(name)
        definitions = []
        if self.progressive_mode != "off":
            definitions.append(SEARCH_TOOLS_DEFINITION)
        definitions.extend(business_definitions(self.gateway, allowed=active))
        definitions.append(READ_RESULT_DEFINITION)
        return definitions

    def _definitions_for(self, state: RunnerState) -> list[dict[str, Any]]:
        if self.progressive_mode == "off":
            return self.definitions
        discovered = tuple(
            name for name in state.get("discovered_tools", [])
            if name in self.searchable_tool_names
        )
        return self._build_definitions(discovered)

    def freeze_reference_date(self, reference_date):
        """A resumed request keeps the date against which its intent was read."""
        self.reference_date = reference_date
        self.gateway.reference_date = reference_date
        self.system_prompt = build_system_prompt(reference_date)
        skill = resolve_agent_skill(self.request)
        if skill:
            self.system_prompt = f"{self.system_prompt}\n\n{skill}"
        if self.progressive_mode != "off":
            self.system_prompt += (
                "\n\n工具发现规则：当前工具列表可能不完整。缺少所需能力时，先调用 "
                "search_tools 搜索工具名、能力或关键词；匹配工具会在下一轮自动加入。"
                "不要猜测未列出的工具名，也不要把搜索工具当成市场数据查询。"
            )

    def enable_answer_delivery(self, emit) -> None:
        """Attach the graph's candidate-answer channel to this runner."""

        self.delivery = AnswerDelivery(
            emit, revision_base=self.delivery.revision,
        )

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

        def on_chunk(chunk) -> None:
            if _cancelled() or perf_counter() >= self.deadline:
                raise InterruptedError("Run cancelled or deadline exceeded")
            self.delivery.feed(_chunk_text(chunk))

        self.delivery.begin_round()
        try:
            repairing = state.get("answer_repair", False)
            definitions = [] if repairing else self._definitions_for(state)
            self._record_usage_context(state, repairing, definitions)
            response = self.provider.generate_messages(
                state["messages"],
                definitions,
                timeout_seconds=max(1, min(35, self.deadline - perf_counter())),
                max_tokens=4096,
                tool_choice="none" if repairing else "auto",
                stage="agent",
                on_chunk=on_chunk,
            )
        except Exception as error:  # noqa: BLE001 - runner boundary reports the failure
            self.delivery.withdraw("回答生成暂时中断")
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
            # The visible draft was cut off; never leave it as if it were an answer.
            self.delivery.withdraw("回答达到长度上限，正在重新整理")
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
            # A tool-selection round is not the answer; withdraw any prose it emitted.
            self.delivery.withdraw("正在继续查询数据")
            return {"messages": messages, "pending": tool_calls, "model_rounds": rounds}
        content = response.content.strip() if isinstance(response.content, str) else ""
        if repairing and not content:
            return {**self._incomplete_stop(state), "model_rounds": rounds}
        # Publish the candidate before a possible compliance review so the reader
        # sees the answer arrive; the committed response still replaces it.
        self.delivery.finish()
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
                self.delivery.withdraw("回答未通过合规核验")
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

    def _record_usage_context(
        self,
        state: RunnerState,
        repairing: bool,
        definitions: list[dict[str, Any]],
    ) -> None:
        """Attach the context cost of this round to the next provider call."""

        tracker = current_llm_usage()
        if tracker is None:
            return
        tracker.set_context(
            stage="length_repair" if repairing else "agent",
            round=state.get("model_rounds", 0) + 1,
            tool_count=0 if repairing else len(definitions),
            tool_definition_chars=0 if repairing else len(json.dumps(
                definitions, ensure_ascii=False, separators=(",", ":"),
            )),
            progressive_mode=self.progressive_mode,
            discovered_tool_count=len(state.get("discovered_tools", [])),
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
        discovered = list(state.get("discovered_tools", []))
        self._active_discovered = discovered
        tool_count = state.get("tool_count", 0)
        if self.progress:
            self.progress("tools", f"正在执行 {len(state['pending'])} 项数据查询")
        meta_tools = {SEARCH_TOOLS_TOOL, READ_RESULT_TOOL}
        for call in state["pending"]:
            is_meta = call["name"] in meta_tools
            is_search = call["name"] == SEARCH_TOOLS_TOOL
            meta_count = sum(1 for name in tool_calls if name in meta_tools)
            if is_search and tool_calls.count(SEARCH_TOOLS_TOOL) >= MAX_SEARCH_CALLS:
                value = {"status": "error", "error": "Tool search budget exhausted"}
            elif is_meta and meta_count >= MAX_META_CALLS:
                value = {"status": "error", "error": "Tool meta-operation budget exhausted"}
            elif not is_meta and tool_count >= MAX_TOOL_CALLS:
                value = {"status": "error", "error": "Tool execution budget exhausted"}
            else:
                # Discovery and receipt paging are meta-operations; they must
                # not consume one of the limited data-query slots.
                if not is_meta:
                    tool_count += 1
                value = self.execute(call)
            if call["name"] == SEARCH_TOOLS_TOOL:
                for row in value.get("rows", []) or []:
                    name = row.get("name") if isinstance(row, dict) else None
                    if (
                        name in self.searchable_tool_names
                        and name not in self.initial_tool_names
                        and name not in discovered
                        and len(set(self.initial_tool_names) | set(discovered))
                        < self.max_active_tools
                    ):
                        discovered.append(name)
                        self._active_discovered = discovered
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
            "discovered_tools": discovered,
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

    def _query_resolves_to_stock(self, query: str) -> bool:
        """Treat a resolvable company name or code as a stock capability hint."""

        resolver = getattr(self.gateway.registry, "resolve_stock_identity", None)
        if not callable(resolver):
            return False
        try:
            resolver(query)
            return True
        except Exception:  # noqa: BLE001 - a non-stock query is the normal case
            return False

    def _search_tools(self, request: SearchToolsRequest) -> dict[str, Any]:
        """Search the enabled catalog and admit only tools that fit the active budget."""

        catalog = {
            name: {
                "description": self.gateway.contracts[name].description,
                "notes": self.gateway.contracts[name].notes,
                "time_mode": self.gateway.contracts[name].time_mode,
            }
            for name in self.searchable_tool_names
            if name in self.gateway.contracts
        }
        active = set(self.initial_tool_names) | set(self._active_discovered)
        remaining = max(0, self.max_active_tools - len(active))
        rows = search_tool_catalog(
            catalog, request.query, limit=request.limit, active_names=active,
        )
        if self._query_resolves_to_stock(request.query):
            # A company name or stock code is itself a capability signal even
            # when its words do not appear in the tool descriptions.
            preferred = (
                "stock_profile", "stock_kline", "stock_financials",
                "stock_news", "stock_activity",
            )
            present = {row["name"] for row in rows}
            rows = [
                *[
                    {
                        "name": name,
                        "description": " ".join(
                            str(catalog[name].get("description") or "").split()
                        )[:180],
                        "time_mode": catalog[name].get("time_mode"),
                        "already_visible": name in active,
                    }
                    for name in preferred
                    if name in catalog and name not in present
                ],
                *rows,
            ]
        visible = [row for row in rows if row["already_visible"]]
        new = [row for row in rows if not row["already_visible"]]
        admitted = new[:remaining]
        selected = [*visible, *admitted][: request.limit]
        if not selected:
            if rows and not admitted and remaining <= 0:
                summary = (
                    f"找到 {len(rows)} 个匹配工具，但活动工具已达上限"
                    f"（{self.max_active_tools}），本次未加入新工具。"
                )
                status = "ok"
            else:
                summary = "没有匹配的工具；请换关键词，或使用当前已可见工具。"
                status = "empty"
        else:
            names = "、".join(row["name"] for row in selected)
            summary = f"找到 {len(selected)} 个工具：{names}。"
            if selected and not admitted and new and remaining <= 0:
                summary += " 活动工具已达上限，本次未加入新工具。"
            elif len(admitted) < len(new):
                summary += f" 仅加入前 {len(admitted)} 个，其余受活动工具上限限制。"
            status = "ok"
        return {
            "name": SEARCH_TOOLS_TOOL,
            "status": status,
            "summary": summary,
            "query": request.query,
            "rows": selected,
            "row_count": len(selected),
            "active_tool_count": len(active),
            "max_active_tools": self.max_active_tools,
        }

    # Validate and execute one call; validation and provider failures stay observations.
    def _execute(self, call: dict[str, Any]) -> dict[str, Any]:
        try:
            if call["name"] == SEARCH_TOOLS_TOOL:
                if self.progressive_mode == "off":
                    raise ValueError("Tool search is disabled for this run")
                request = SearchToolsRequest.model_validate(call.get("args") or {})
                return self._search_tools(request)
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
        self.delivery.withdraw("任务已取消")
        return {
            "answer": "任务已取消，未继续执行新的数据查询。",
            "status": "cancelled",
            "stop_reason": "cancelled",
            "done": True,
        }

    def _budget_stop(self, state: RunnerState) -> RunnerState:
        self.delivery.withdraw("查询预算已用完")
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

        self.delivery.withdraw("回答未完整生成")
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
        self.delivery.withdraw("回答合规检查未完成")
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


def _chunk_text(chunk: Any) -> str:
    """Return visible text from one native model chunk, never tool-call JSON."""

    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str)
            else str(block.get("text", "")) if isinstance(block, dict) and block.get("type") == "text"
            else ""
            for block in content
        )
    return ""


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
