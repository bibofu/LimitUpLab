"""Application-lifetime durable ReAct graph with a synchronous worker bridge."""

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
from time import perf_counter

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agents.run_lifecycle import CURRENT_CONTROL
from app.agents.runtime_errors import RecoverableRunError
from app.agents.runner.runner import RunnerState
from app.agents.runner.result import RunnerResult
from app.agents.runner.security import review_input_security
from app.agents.runner.guardrails import REFUSAL
from app.agents.runner.guardrails import MAX_TOOL_CALLS
from app.services.durable_checkpointer import FencedPostgresSaver, psycopg_url
from app.services.runtime_coordination import LeaseLost

SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)
_runtime = None


def get_runtime():
    return _runtime


@dataclass
class RunDependencies:
    runner: object
    control: object


@contextmanager
def execution_context(deps):
    token = CURRENT_CONTROL.set(deps.control)
    try:
        yield
    finally:
        CURRENT_CONTROL.reset(token)


def _review(state, deps):
    from app.agents.memory_commands import prepare_memory_request, retrieve_for_run
    with execution_context(deps):
        if deps.control.cancelled():
            return deps.runner._cancelled_stop()
        retrieved = retrieve_for_run(deps.control, state["current_query"])
        try:
            decision = review_input_security(deps.runner.provider, message=state["current_query"], timeout_seconds=10)
        except Exception as error:
            from app.agents.runtime_errors import RecoverableRunError
            raise RecoverableRunError("输入审核暂时中断，尚未执行工具。") from error
        if decision.decision == "refuse":
            return {"answer": REFUSAL if "trading_advice" in decision.signals else
                    "我可以协助查询有来源的股票研究事实，不能执行绕过系统边界的指令。",
                    "status": "refuse", "stop_reason": "input_policy", "done": True}
        proposal = prepare_memory_request(decision.memory_intent, deps.control, retrieved.owner_revision) \
            if decision.memory_intent else {}
        return {"done": False, "memory_request": proposal,
                "memory_owner_revision": retrieved.owner_revision,
                "memory_context_fingerprint": retrieved.context_fingerprint}


def _agent(state, deps):
    from app.agents.context_builder import build_context, ContextBudgetExceeded, ContextMessageError
    from app.agents.memory_commands import retrieve_for_run
    from app.services.memory_scope import clean_conversation_for_memory_revision, sanitize_working_messages_for_memory_revision
    with execution_context(deps):
        runner = deps.runner
        runner.deadline = perf_counter() + state["remaining_seconds"]
        started = perf_counter()
        page = {**runner.request.page_context}
        if runner.request.symbol:
            page["symbol"] = runner.request.symbol
        if runner.request.trade_date:
            page["trade_date"] = runner.request.trade_date.isoformat()
        retrieved = retrieve_for_run(deps.control, state["current_query"])
        conversation = clean_conversation_for_memory_revision(state["conversation"], retrieved)
        working = sanitize_working_messages_for_memory_revision(
            state.get("messages", []), retrieved, state.get("memory_context_fingerprint"),
        )
        try:
            built = build_context(
                system_prompt=runner.messages[0].content, query=state["current_query"],
                conversation=conversation, working_messages=working, long_term_memory=retrieved,
                page_context=page, tool_definitions=runner.definitions,
            )
        except ContextBudgetExceeded:
            return {"answer": "本次查询结果超出上下文预算，请缩小日期范围或分批查询；完整工具结果仍保留在证据中。",
                    "status": "partial", "stop_reason": "context_budget", "done": True}
        except ContextMessageError:
            return {"answer": "本轮工具消息不完整，无法继续生成可核验回答，请重新发起查询。",
                    "status": "error", "stop_reason": "invalid_tool_context", "done": True}
        if built.clarification:
            return {"answer": built.clarification, "status": "clarify", "stop_reason": "reference_ambiguous",
                    "done": True, "context_diagnostics": built.diagnostics}
        diagnostics = {**built.diagnostics, "thread_id": deps.control.row["session_id"],
                       "run_id": deps.control.row["run_id"], "prompt_version": "durable-react-v2-memory",
                       "summary_triggered": bool(conversation.get("summary_triggered")),
                       "recovery_count": deps.control.row.get("recovery_count", 0)}
        update = runner.agent({**state, "messages": built.messages, "context_diagnostics": diagnostics})
        if "messages" in update:
            update["messages"] = [*working, *update["messages"][len(built.messages):]]
        update.update(conversation=conversation, memory_owner_revision=retrieved.owner_revision,
                      memory_context_fingerprint=retrieved.context_fingerprint,
                      memory_context_used=bool(built.diagnostics.get("memory_hit_count", 0)))
        update["context_diagnostics"] = diagnostics
        update["resolved_reference"] = built.resolved_reference or {}
        update["remaining_seconds"] = max(0, state["remaining_seconds"] - (perf_counter() - started))
        return update


def _tool(state, deps):
    with execution_context(deps):
        started = perf_counter()
        runner = deps.runner
        if state["remaining_seconds"] <= 0:
            return runner._budget_stop(state)
        runner.deadline = perf_counter() + state["remaining_seconds"]
        # Each call has its own super-step. Its durable receipt is committed
        # before the checkpoint; a crash between them reuses the exact receipt.
        update = runner.tools({**state, "pending": state["pending"][:1]})
        if update.get("tool_messages") and not update.get("done"):
            value = update["tool_messages"][-1]
            if (state.get("tool_count", 0) < MAX_TOOL_CALLS
                    and value.get("error_type") != "UncertainExecution"):
                update["tool_messages"][-1] = {
                    "result_ref": state["pending"][0]["id"], "name": state["pending"][0]["name"],
                    "status": value.get("status"), "summary": value.get("summary"),
                }
        update["pending"] = state["pending"][1:]
        update["remaining_seconds"] = max(0, state["remaining_seconds"] - (perf_counter() - started))
        return update


def build_graph(saver):
    graph = StateGraph(RunnerState, context_schema=RunDependencies)

    async def review(state, runtime: Runtime[RunDependencies]):
        return await asyncio.to_thread(_review, state, runtime.context)

    async def agent(state, runtime: Runtime[RunDependencies]):
        return await asyncio.to_thread(_agent, state, runtime.context)

    async def tool(state, runtime: Runtime[RunDependencies]):
        return await asyncio.to_thread(_tool, state, runtime.context)

    async def memory(state, runtime: Runtime[RunDependencies]):
        from app.agents.memory_commands import execute_memory_request
        deps = runtime.context
        with execution_context(deps):
            if deps.control.cancelled():
                return deps.runner._cancelled_stop()
            return await asyncio.to_thread(execute_memory_request, state, deps.control)

    graph.add_node("input_review", review)
    graph.add_node("agent", agent)
    graph.add_node("tool", tool)
    graph.add_node("memory", memory)
    graph.add_edge(START, "input_review")
    graph.add_conditional_edges("input_review", lambda s: END if s.get("done") else "memory" if s.get("memory_request") else "agent")
    graph.add_edge("memory", END)
    graph.add_conditional_edges("agent", lambda s: END if s.get("done") else "tool" if s.get("pending") else "agent")
    graph.add_conditional_edges("tool", lambda s: END if s.get("done") else "tool" if s.get("pending") else "agent")
    return graph.compile(checkpointer=saver)


class DurableRuntime:
    def __init__(self, pool):
        self.pool = pool
        self.loop = asyncio.get_running_loop()
        self.saver = FencedPostgresSaver(pool)
        self.graph = build_graph(self.saver)
        self.active_tasks = set()

    @classmethod
    async def open(cls, database_url):
        pool = AsyncConnectionPool(psycopg_url(database_url), min_size=1, max_size=8,
                                   open=False, kwargs={"autocommit": True, "row_factory": dict_row,
                                                       "prepare_threshold": 0})
        await pool.open(wait=True)
        try:
            async with pool.connection() as conn:
                result = await conn.execute("SELECT MAX(v) AS v FROM checkpoint_migrations")
                if (await result.fetchone())["v"] != 9:
                    raise RuntimeError("Checkpoint schema version is incompatible; run Alembic")
            return cls(pool)
        except BaseException:
            await pool.close()
            raise

    def run_sync(self, runner, control):
        try:
            return asyncio.run_coroutine_threadsafe(self.run(runner, control), self.loop).result()
        except FutureCancelledError as error:
            raise RecoverableRunError("服务正在重启，原任务将在恢复后继续。") from error

    async def run(self, runner, control):
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            return await self._run(runner, control)
        except (LeaseLost, RecoverableRunError):
            raise
        except Exception as error:
            logger.exception("Durable state preparation interrupted run_id=%s", control.row["run_id"])
            raise RecoverableRunError("执行状态暂时无法读取，原任务可恢复。") from error
        finally:
            self.active_tasks.discard(task)

    async def _run(self, runner, control):
        from app.agents.runtime_errors import RecoverableRunError
        lease = control.execution
        if lease is None:
            raise LeaseLost("Durable execution requires a lease")
        config = {"configurable": {"thread_id": control.row["session_id"], "run_id": control.row["run_id"],
                    "owner_id": control.row["owner_id"], "lease_token": lease.token, "fence": lease.fence},
                  "recursion_limit": 40}
        runner.durable = True
        snapshot = await self.graph.aget_state(config)
        state = snapshot.values
        if state and state.get("schema_version") != SCHEMA_VERSION:
            raise RecoverableRunError("会话状态版本不兼容，需要迁移后恢复。")
        if state and state.get("run_id") == control.row["run_id"]:
            if snapshot.next:
                initial = None
            else:
                return await asyncio.to_thread(result_from_state, state, runner, control)
        else:
            conversation = await asyncio.to_thread(self._conversation, state, runner, control)
            initial = {"schema_version": SCHEMA_VERSION, "run_id": control.row["run_id"],
                       "current_query": runner.request.message, "conversation": conversation,
                       "messages": [], "pending": [], "tool_calls": [], "tool_statuses": [],
                       "tool_messages": [], "model_rounds": 0, "tool_count": 0, "done": False,
                       "answer": "", "status": "", "stop_reason": "", "error": "",
                       "answer_repair": False, "compliance_reviewed": False,
                       "context_diagnostics": {}, "resolved_reference": {},
                       "memory_request": {}, "memory_action": {}, "memory_owner_revision": 0,
                       "memory_context_fingerprint": "", "memory_context_used": False,
                       "remaining_seconds": max(0, runner.deadline - perf_counter())}
        try:
            final = await self.graph.ainvoke(initial, config, context=RunDependencies(runner, control),
                                             durability="sync")
        except (LeaseLost, RecoverableRunError):
            raise
        except Exception as error:
            logger.exception("Durable graph interrupted run_id=%s", control.row["run_id"])
            raise RecoverableRunError("执行暂时中断，已保存可恢复进度。") from error
        return await asyncio.to_thread(result_from_state, final, runner, control)

    def _conversation(self, state, runner, control):
        from app.agents.conversation_memory import compress_conversation
        from app.agents.structured_references import references_from_committed_result
        from app.repositories.chat_memory_repository import ChatMemoryRepository
        from app.services.session_memory import memory_prompt_payload
        from app.agents.memory_commands import retrieve_for_run
        from app.services.memory_scope import clean_conversation_for_memory_revision
        conversation = dict(state.get("conversation", {})) if state else {}
        if not state:
            old = ChatMemoryRepository(control.journal.database_url).get_memory(
                control.row["session_id"], owner_id=control.row["owner_id"])
            if old:
                payload = memory_prompt_payload(old)
                conversation = {"conversation_summary": payload["summary"],
                                "active_constraints": payload["constraints"], "research_context": payload,
                                "imported_from": old.memory_version}
        # Only committed business messages can enter the next turn's context.
        messages = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in runner.history]
        if not state and old and old.last_message_id:
            ids = [item["message_id"] for item in messages]
            if old.last_message_id in ids:
                messages = messages[ids.index(old.last_message_id) + 1:]
            conversation.update(summary_cursor=old.last_message_id,
                                summarized_message_count=old.summarized_message_count)
        # Revoke stale derived text BEFORE any summarizer model call. Cleaning
        # only after compression would already have sent deleted preferences.
        conversation = clean_conversation_for_memory_revision(
            conversation, retrieve_for_run(control, runner.request.message),
        )
        if messages:
            conversation = compress_conversation(conversation, messages, provider=runner.provider)
        if state and state.get("run_id"):
            previous = control.journal.get(state["run_id"], control.row["owner_id"])
            if not previous.get("response_json"):
                raise RecoverableRunError("上一轮尚未正式交付，无法开始下一轮。")
            response = json.loads(previous["response_json"])
            if response.get("task_status") in {"complete", "partial", "empty"}:
                references = references_from_committed_result(state["run_id"], response)
                if references:
                    conversation["active_references"] = references
        return clean_conversation_for_memory_revision(conversation, retrieve_for_run(control, runner.request.message))


def result_from_state(state, runner, control):
    from app.repositories.tool_result_repository import ToolResultRepository
    from app.agents.memory_commands import retrieve_for_run
    retrieved = retrieve_for_run(control, state["current_query"])
    # A final checkpoint may outlive a deletion/update/expiry during downtime.
    # Never hand its obsolete draft to the delivery transaction after recovery.
    if (state.get("memory_context_fingerprint")
            and retrieved.context_fingerprint != state["memory_context_fingerprint"]):
        return RunnerResult(answer="长期记忆在本次回答生成后发生变化，请重新提问以使用当前有效的记忆。",
                            status="partial", stop_reason="memory_changed",
                            memory_action=state.get("memory_action") or None,
                            memory_owner_revision=retrieved.owner_revision,
                            memory_context_fingerprint=retrieved.context_fingerprint)
    repository = ToolResultRepository(control)
    observations = [{**repository.read(item["result_ref"]), "name": item["name"]}
                    if item.get("result_ref") else item for item in state.get("tool_messages", [])]
    return RunnerResult(answer=state.get("answer", ""), status=state.get("status", "error"),
                        stop_reason=state.get("stop_reason", "unknown"), tool_calls=state.get("tool_calls", []),
                        tool_messages=observations, model_rounds=state.get("model_rounds", 0),
                        tool_count=state.get("tool_count", 0), error=state.get("error"),
                        compliance_reviewed=state.get("compliance_reviewed", False),
                        memory_action=state.get("memory_action") or None,
                        memory_owner_revision=state.get("memory_owner_revision"),
                        memory_context_fingerprint=state.get("memory_context_fingerprint") or None,
                        memory_context_used=state.get("memory_context_used", False),
                        duration_ms=round((perf_counter() - runner.started) * 1000))
