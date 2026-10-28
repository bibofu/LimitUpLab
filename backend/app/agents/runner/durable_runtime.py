"""Application-lifetime durable ReAct graph with a synchronous worker bridge."""

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from time import perf_counter
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agents.run_lifecycle import CURRENT_CONTROL
from app.agents.context_builder import CONTEXT_VERSION
from app.agents.runtime_errors import RecoverableRunError, RunPauseRequested
from app.agents.run_pause import PausedRunError, PausedRunOutcome
from app.agents.runner.runner import RunnerState
from app.agents.runner.result import RunnerResult
from app.agents.runner.security import review_input_security
from app.agents.runner.guardrails import REFUSAL
from app.agents.runner.guardrails import MAX_TOOL_CALLS
from app.services.durable_checkpointer import FencedPostgresSaver, psycopg_url
from app.services.runtime_coordination import LeaseLost
from app.services.agent_stream import NativeStreamBridge, emit_status, emit_tool_event
from app.services.agent_tracing import trace_context, trace_stage

SCHEMA_VERSION = 1
PROMPT_VERSION = "durable-react-v5-row-positions"
logger = logging.getLogger(__name__)
_runtime = None


def get_runtime():
    return _runtime


def _config(control):
    lease = control.execution
    return {"configurable": {"thread_id": control.row["session_id"], "run_id": control.row["run_id"],
                "owner_id": control.row["owner_id"], "lease_token": lease.token if lease else None,
                "fence": lease.fence if lease else None}, "recursion_limit": 40}


def _reference_date(state, control):
    if state.get("reference_date"):
        return date.fromisoformat(state["reference_date"])
    started = control.row["started_at"]
    if isinstance(started, str):
        started = datetime.fromisoformat(started)
    if started.tzinfo is None:
        started = started.replace(tzinfo=ZoneInfo("UTC"))
    return started.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _pause_interrupt(snapshot, run_id):
    interruptions = snapshot.interrupts
    if not interruptions:
        return None
    if (len(interruptions) != 1 or snapshot.values.get("run_id") != run_id
            or not isinstance(interruptions[0].value, dict)
            or interruptions[0].value.get("kind") != "run_pause"
            or interruptions[0].value.get("run_id") != run_id):
        raise RecoverableRunError("存在无法识别的暂停检查点，原任务仍可恢复。")
    return interruptions[0]


def _paused_outcome(snapshot, control, revision):
    if snapshot.values.get("run_id") != control.row["run_id"]:
        return PausedRunOutcome(control.row["run_id"], None, revision, "checkpoint")
    checkpoint_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
    if not checkpoint_id:
        raise RecoverableRunError("暂停检查点尚未确认写入，原任务仍可恢复。")
    paused = _pause_interrupt(snapshot, control.row["run_id"])
    mode = "interrupt" if paused else "delivery" if snapshot.values.get("done") and not snapshot.next else "checkpoint"
    return PausedRunOutcome(control.row["run_id"], checkpoint_id, revision, mode,
                            paused.id if paused else None)


@dataclass
class RunDependencies:
    runner: object
    control: object
    resume_pause_revision: int | None = None


@contextmanager
def execution_context(deps):
    token = CURRENT_CONTROL.set(deps.control)
    try:
        yield
    finally:
        CURRENT_CONTROL.reset(token)


def _retrieve(control, query):
    from app.agents.memory_commands import retrieve_for_run
    with trace_stage("memory_retrieval") as details:
        result = retrieve_for_run(control, query)
        details.update(result.diagnostics)
        return result


def _review(state, deps):
    from app.agents.memory_commands import prepare_memory_request
    from app.agents.structured_references import compact_referent_hint
    with execution_context(deps), trace_stage("input_review", {"node": "input_review"}):
        if deps.control.cancelled():
            return deps.runner._cancelled_stop()
        retrieved = _retrieve(deps.control, state["current_query"])
        try:
            decision = review_input_security(
                deps.runner.provider, message=state["current_query"], timeout_seconds=10,
                referent_hint=compact_referent_hint(state.get("conversation", {})),
            )
        except Exception as error:
            from app.agents.runtime_errors import RecoverableRunError
            raise RecoverableRunError("输入审核暂时中断，尚未执行工具。") from error
        if decision.decision == "refuse":
            trading_advice = "trading_advice" in decision.signals
            return {"answer": REFUSAL if trading_advice else
                    "我可以协助查询有来源的股票研究事实，不能执行绕过系统边界的指令。",
                    "status": "refuse", "stop_reason": "input_trading_advice" if trading_advice else "input_policy", "done": True}
        proposal = prepare_memory_request(decision.memory_intent, deps.control, retrieved.owner_revision) \
            if decision.memory_intent else {}
        return {"done": False, "memory_request": proposal,
                "reference_intent": decision.reference_intent.model_dump(),
                "memory_owner_revision": retrieved.owner_revision,
                "memory_context_fingerprint": retrieved.context_fingerprint,
                "memory_dependencies": list(retrieved.dependencies)}


def _agent(state, deps):
    from app.agents.context_builder import build_context, ContextBudgetExceeded, ContextMessageError
    from app.services.memory_scope import clean_conversation_for_memory_revision, sanitize_working_messages_for_memory_revision
    with execution_context(deps):
        runner = deps.runner
        runner.deadline = perf_counter() + state["remaining_seconds"]
        started = perf_counter()
        from app.agents.session_history import (
            format_session_recap, is_session_recap_query, session_facts_cover_query,
        )
        facts = (state.get("conversation") or {}).get("session_facts") or {}
        if (is_session_recap_query(state["current_query"])
                and session_facts_cover_query(state["current_query"])
                and any(facts.get(key) for key in (
                    "first_detailed_stock", "first_explicit_date",
                    "compared_indices", "dragon_tiger_followup_stock",
                ))):
            # A recap is a deterministic index lookup, not another market
            # query. Do not let the model guess history from a compressed
            # summary when the committed ledger already proves the objects.
            return {
                "answer": format_session_recap(facts),
                "status": "answered", "stop_reason": "answered", "done": True,
                "memory_context_used": False, "display_policies": [],
                "context_diagnostics": {"session_recap": True},
                "remaining_seconds": max(
                    0, state["remaining_seconds"] - (perf_counter() - started)
                ),
            }
        # Pre-v4 pending checkpoints have no reviewed reference meaning. Review
        # once before another model call; never restore the old ordinal heuristic.
        if state.get("reference_intent") is None:
            reviewed = _review(state, deps)
            if reviewed.get("done"):
                return reviewed
            if reviewed.get("memory_request"):
                return {"answer": "旧任务的上下文协议已升级，请重新提交记忆管理请求。",
                        "status": "partial", "stop_reason": "context_upgrade", "done": True}
            state = {**state, **reviewed}
        page = {**runner.request.page_context}
        if runner.request.symbol:
            page["symbol"] = runner.request.symbol
        if runner.request.trade_date:
            page["trade_date"] = runner.request.trade_date.isoformat()
        retrieved = _retrieve(deps.control, state["current_query"])
        from app.services.memory_display import display_answer_rule, display_policies_for
        display_policies = display_policies_for(retrieved.items, state["current_query"])
        system_prompt = runner.system_prompt + display_answer_rule(display_policies)
        conversation = clean_conversation_for_memory_revision(state["conversation"], retrieved)
        working = sanitize_working_messages_for_memory_revision(
            state.get("messages", []), retrieved, state.get("memory_context_fingerprint"),
            state.get("memory_dependencies"),
        )
        try:
            with trace_stage("context_preparation", {"node": "agent"}) as details:
                built = build_context(
                    system_prompt=system_prompt, query=state["current_query"],
                    conversation=conversation, working_messages=working, long_term_memory=retrieved,
                    page_context=page, tool_definitions=runner.definitions,
                    working_memory_dependencies=state.get("memory_dependencies"),
                    reference_intent=state["reference_intent"],
                )
                details.update(built.diagnostics)
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
                       "run_id": deps.control.row["run_id"], "prompt_version": PROMPT_VERSION,
                       "summary_triggered": bool(conversation.get("summary_triggered")),
                       "recovery_count": deps.control.row.get("recovery_count", 0)}
        with trace_context(**diagnostics):
            update = runner.agent({**state, "messages": built.messages, "context_diagnostics": diagnostics})
        if "messages" in update:
            update["messages"] = [*working, *update["messages"][len(built.messages):]]
        update.update(conversation=conversation, memory_owner_revision=retrieved.owner_revision,
                      memory_context_fingerprint=retrieved.context_fingerprint,
                      memory_dependencies=list(retrieved.dependencies),
                      display_policies=display_policies,
                      memory_context_used=bool(built.diagnostics.get("memory_hit_count", 0)))
        update["context_diagnostics"] = diagnostics
        update["resolved_reference"] = built.resolved_reference or {}
        update["reference_intent"] = state["reference_intent"]
        update["remaining_seconds"] = max(0, state["remaining_seconds"] - (perf_counter() - started))
        return update


def _tool(state, deps):
    call = state["pending"][0]
    with execution_context(deps), trace_stage("tool_execution", {
        "node": "tool", "tool_call_id": call["id"], "tool_name": call["name"],
    }):
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

    async def gate(name, runtime):
        # The DB control read is the start boundary. An already-started call is
        # allowed to finish and checkpoint; the following node checks again.
        signal = await asyncio.to_thread(runtime.context.control.pause_state)
        if signal["requested"]:
            return {"pause_next_node": name, "pause_revision": signal["revision"]}
        return None

    def pause(state, runtime: Runtime[RunDependencies]):
        # LangGraph replays this node on resume. Keep it free of model calls,
        # tools, business writes, progress events, and mutable control reads.
        # A process can die after the gate's checkpoint but before interrupt().
        # Explicitly resumed ordinary checkpoints must not require a second click.
        resumed = {"pause_revision": state["pause_revision"]} if (
            runtime.context.resume_pause_revision == state["pause_revision"]
        ) else interrupt({"kind": "run_pause", "run_id": state["run_id"],
                          "revision": state["pause_revision"]})
        if resumed != {"pause_revision": state["pause_revision"]}:
            raise RecoverableRunError("暂停恢复标识不匹配，原任务仍可恢复。")
        return Command(goto=state["pause_next_node"],
                       update={"pause_next_node": "", "pause_revision": 0})

    async def review(state, runtime: Runtime[RunDependencies]):
        paused = await gate("input_review", runtime)
        if paused:
            return paused
        emit_status("planning")
        return await asyncio.to_thread(_review, state, runtime.context)

    async def agent(state, runtime: Runtime[RunDependencies]):
        paused = await gate("agent", runtime)
        if paused:
            return paused
        return await asyncio.to_thread(_agent, state, runtime.context)

    async def tool(state, runtime: Runtime[RunDependencies]):
        paused = await gate("tool", runtime)
        if paused:
            return paused
        if (state["remaining_seconds"] <= 0 or state.get("tool_count", 0) >= MAX_TOOL_CALLS
                or await asyncio.to_thread(runtime.context.control.cancelled)):
            return await asyncio.to_thread(_tool, state, runtime.context)
        call = state["pending"][0]
        emit_tool_event("tool_start", call)
        update = await asyncio.to_thread(_tool, state, runtime.context)
        observations = update.get("tool_messages", [])
        status = observations[-1].get("status") if observations else None
        emit_tool_event("tool_end", call, status=status)
        if status in {"partial", "error"}:
            runtime.stream_writer({"kind": "warning", "stage": "tools", "node": "tool"})
        return update

    async def memory(state, runtime: Runtime[RunDependencies]):
        paused = await gate("memory", runtime)
        if paused:
            return paused
        from app.agents.memory_commands import execute_memory_request
        deps = runtime.context
        emit_status("checking")
        with execution_context(deps):
            if deps.control.cancelled():
                return deps.runner._cancelled_stop()
            return await asyncio.to_thread(execute_memory_request, state, deps.control)

    graph.add_node("input_review", review)
    graph.add_node("agent", agent)
    graph.add_node("tool", tool)
    graph.add_node("memory", memory)
    graph.add_node("pause", pause)
    graph.add_edge(START, "input_review")
    graph.add_conditional_edges("input_review", lambda s: "pause" if s.get("pause_next_node") else
                                END if s.get("done") else "memory" if s.get("memory_request") else "agent")
    graph.add_conditional_edges("memory", lambda s: "pause" if s.get("pause_next_node") else END)
    graph.add_conditional_edges("agent", lambda s: "pause" if s.get("pause_next_node") else
                                END if s.get("done") else "tool" if s.get("pending") else "agent")
    graph.add_conditional_edges("tool", lambda s: "pause" if s.get("pause_next_node") else
                                END if s.get("done") else "tool" if s.get("pending") else "agent")
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

    def pause_sync(self, control):
        """Reconcile a pause without starting or resuming any graph node."""
        return asyncio.run_coroutine_threadsafe(self.pause(control), self.loop).result()

    async def pause(self, control):
        snapshot = await self.graph.aget_state(_config(control))
        signal = await asyncio.to_thread(control.pause_state)
        return _paused_outcome(snapshot, control, signal["revision"])

    def validate_resume_sync(self, control):
        try:
            return asyncio.run_coroutine_threadsafe(self.validate_resume(control), self.loop).result()
        except RecoverableRunError:
            raise
        except Exception as error:
            raise RecoverableRunError("继续检查点暂时无法核验，暂停状态已保留。") from error

    async def validate_resume(self, control):
        """Verify the user-confirmed location before marking resuming as running.

        This is a one-time dispatch check. Crash recovery after that transition
        may legitimately have advanced past the formerly paused checkpoint.
        """
        if control.row["run_status"] != "resuming":
            raise RecoverableRunError("任务尚未授权继续，暂停状态已保留。")
        actual = _paused_outcome(await self.graph.aget_state(_config(control)), control,
                                 control.row["control_revision"])
        expected = (control.row.get("pause_checkpoint_id"), control.row.get("pause_mode"),
                    control.row.get("pause_interrupt_id"))
        if (actual.checkpoint_id, actual.resume_mode, actual.interrupt_id) != expected:
            raise RecoverableRunError("继续位置与已确认的暂停检查点不一致，原任务已保留。")

    async def run(self, runner, control):
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            with trace_context(run_id=control.row["run_id"], thread_id=control.row["session_id"],
                               session_id=control.row["session_id"], runtime_version="durable-v1",
                               model_version=getattr(runner.provider, "model", "unknown"),
                               prompt_version=PROMPT_VERSION, context_version=CONTEXT_VERSION,
                               recovery_count=control.row.get("recovery_count", 0)):
                return await self._run(runner, control)
        except RunPauseRequested:
            raise PausedRunError(await self.pause(control)) from None
        except (LeaseLost, RecoverableRunError, PausedRunError):
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
        config = _config(control)
        bridge = NativeStreamBridge(control, [item.get("function", {}).get("name") for item in runner.definitions])
        with trace_stage("checkpoint_resume", {"operation": "read"}) as details:
            snapshot = await self.graph.aget_state(config)
            details["checkpoint_id"] = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
            details["resume_mode"] = "resume" if snapshot.values.get("run_id") == control.row["run_id"] else "new_turn"
        state = snapshot.values
        if state and state.get("schema_version") != SCHEMA_VERSION:
            raise RecoverableRunError("会话状态版本不兼容，需要迁移后恢复。")
        signal = await asyncio.to_thread(control.pause_state)
        if signal["requested"]:
            raise PausedRunError(_paused_outcome(snapshot, control, signal["revision"]))
        same_run = state.get("run_id") == control.row["run_id"]
        runner.freeze_reference_date(_reference_date(state if same_run else {}, control))
        resume_pause_revision = None
        if state and state.get("run_id") == control.row["run_id"]:
            if snapshot.next:
                paused = _pause_interrupt(snapshot, control.row["run_id"])
                initial = Command(resume={"pause_revision": paused.value["revision"]}) if paused else None
                if not paused and snapshot.next == ("pause",):
                    resume_pause_revision = state["pause_revision"]
                await bridge.publish({"kind": "status", "stage": "preparing", "phase": "resume"})
            else:
                if not state.get("done"):
                    raise RecoverableRunError("执行状态尚未完整提交，原任务可恢复。")
                await bridge.publish({"kind": "status", "stage": "checking", "phase": "delivery_retry"})
                with trace_stage("final_validation", {"resume_mode": "delivery_retry"}):
                    return await asyncio.to_thread(result_from_state, state, runner, control)
        else:
            with trace_stage("context_preparation", {"operation": "conversation"}) as details:
                conversation = await asyncio.to_thread(self._conversation, state, runner, control)
                details["summary_triggered"] = bool(conversation.get("summary_triggered"))
            initial = {"schema_version": SCHEMA_VERSION, "run_id": control.row["run_id"],
                       "reference_date": runner.reference_date.isoformat(), "pause_next_node": "", "pause_revision": 0,
                       "current_query": runner.request.message, "conversation": conversation,
                       "messages": [], "pending": [], "tool_calls": [], "tool_statuses": [],
                       "tool_messages": [], "model_rounds": 0, "tool_count": 0, "done": False,
                       "answer": "", "status": "", "stop_reason": "", "error": "",
                       "answer_repair": False, "compliance_reviewed": False,
                       "context_diagnostics": {}, "resolved_reference": {}, "reference_intent": None,
                       "memory_request": {}, "memory_action": {}, "memory_owner_revision": 0,
                       "memory_context_fingerprint": "", "memory_dependencies": [],
                       "display_policies": [], "memory_context_used": False,
                       "remaining_seconds": max(0, runner.deadline - perf_counter())}
        previous_progress = runner.progress
        runner.progress = emit_status
        try:
            # In pinned LangGraph 1.0.5 multiple stream modes yield (mode, data).
            # Updates can precede their sync checkpoint: never deliver from them.
            async for mode, chunk in self.graph.astream(initial, config, context=RunDependencies(runner, control, resume_pause_revision),
                                                        stream_mode=["custom", "updates"], durability="sync"):
                await bridge.consume(mode, chunk)
            with trace_stage("checkpoint_read", {"operation": "verify_final"}) as details:
                completed = await self.graph.aget_state(config)
                details["checkpoint_id"] = (completed.config or {}).get("configurable", {}).get("checkpoint_id")
            final = completed.values
            paused = _pause_interrupt(completed, control.row["run_id"])
            signal = await asyncio.to_thread(control.pause_state)
            if paused or signal["requested"]:
                raise PausedRunError(_paused_outcome(completed, control, signal["revision"]))
            if completed.next or final.get("run_id") != control.row["run_id"] or not final.get("done"):
                raise RecoverableRunError("执行状态尚未完整提交，原任务可恢复。")
        except (LeaseLost, RecoverableRunError, PausedRunError, RunPauseRequested):
            raise
        except Exception as error:
            logger.exception("Durable graph interrupted run_id=%s", control.row["run_id"])
            raise RecoverableRunError("执行暂时中断，已保存可恢复进度。") from error
        finally:
            runner.progress = previous_progress
        with trace_stage("final_validation", {"tool_count": final.get("tool_count", 0),
                                               "model_rounds": final.get("model_rounds", 0)}):
            return await asyncio.to_thread(result_from_state, final, runner, control)

    def _conversation(self, state, runner, control):
        from app.agents.conversation_memory import compress_conversation
        from app.agents.structured_references import (
            build_conversation_referents, constraints_from_references,
            references_from_committed_result,
        )
        from app.repositories.chat_memory_repository import ChatMemoryRepository
        from app.services.conversation_summary import summary_payload
        from app.services.memory_scope import clean_conversation_for_memory_revision
        from app.agents.session_history import (
            build_session_facts, build_session_ledger, is_session_recap_query,
        )
        conversation = dict(state.get("conversation", {})) if state else {}
        if not state:
            old = ChatMemoryRepository(control.lifecycle.database_url).get_memory(
                control.row["session_id"], owner_id=control.row["owner_id"])
            if old:
                payload = summary_payload(old)
                conversation = {"conversation_summary": payload["summary"],
                                "active_constraints": payload["constraints"], "research_context": payload,
                                "imported_from": old.memory_version}
        # Only committed business messages can enter the next turn's context.
        messages = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in runner.history]
        if is_session_recap_query(runner.request.message):
            # A compact, deterministic turn index prevents the model from
            # claiming that an earlier committed turn never happened. The
            # full chat ledger remains in PostgreSQL; this is a bounded view.
            conversation["session_ledger"] = build_session_ledger(messages)
            conversation["session_facts"] = build_session_facts(conversation["session_ledger"])
        if not state and old and old.last_message_id:
            ids = [item["message_id"] for item in messages]
            if old.last_message_id in ids:
                messages = messages[ids.index(old.last_message_id) + 1:]
            conversation.update(summary_cursor=old.last_message_id,
                                summarized_message_count=old.summarized_message_count)
        # Revoke stale derived text BEFORE any summarizer model call. Cleaning
        # only after compression would already have sent deleted preferences.
        conversation = clean_conversation_for_memory_revision(
            conversation, _retrieve(control, runner.request.message),
        )
        if messages:
            conversation = compress_conversation(conversation, messages, provider=runner.provider)
        if state and state.get("run_id"):
            previous = control.lifecycle.get(state["run_id"], control.row["owner_id"])
            if not previous.get("response_json"):
                raise RecoverableRunError("上一轮尚未正式交付，无法开始下一轮。")
            response = json.loads(previous["response_json"])
            if response.get("task_status") in {"complete", "partial", "empty"}:
                references = references_from_committed_result(state["run_id"], response)
                if references:
                    conversation["active_references"] = references
                    conversation["active_constraints"] = constraints_from_references(references)
                # Referents are pointers for follow-up questions, not market
                # evidence. Build them at run level so several single-stock
                # tool calls form one coherent entity set.
                conversation["referents"] = build_conversation_referents(
                    state["run_id"], response,
                )
            else:
                conversation["referents"] = {}
        return clean_conversation_for_memory_revision(conversation, _retrieve(control, runner.request.message))


def result_from_state(state, runner, control):
    from app.agents.result_paging import attach_result_reference
    from app.agents.grounding import missing_required_tool, required_tool_for_query
    from app.repositories.long_term_memory_repository import LongTermMemoryRepository
    from app.repositories.tool_result_repository import ToolResultRepository
    if (not state.get("memory_action")
            and missing_required_tool(
                state.get("current_query") or "", state.get("tool_calls"),
                state.get("status"), answer=state.get("answer"),
            )):
        required_tool = required_tool_for_query(state.get("current_query") or "")
        return RunnerResult(
            answer=(
                f"这个问题需要本轮 {required_tool} 工具的可核验结果，"
                "当前回答没有相应工具调用。为避免复用旧数据，本次未发布结论；"
                "请重新提问，我会先查询再回答。"
            ),
            status="partial",
            stop_reason="tool_evidence_required",
            memory_action=state.get("memory_action") or None,
            memory_owner_revision=state.get("memory_owner_revision"),
            memory_context_fingerprint=state.get("memory_context_fingerprint") or None,
            memory_context_used=False,
            duration_ms=round((perf_counter() - runner.started) * 1000),
        )
    dependencies = list(state.get("memory_dependencies") or [])
    changed = False
    if dependencies:
        changed = not LongTermMemoryRepository(
            control.lifecycle.database_url,
        ).dependencies_unchanged(control.row["owner_id"], dependencies)
    elif state.get("memory_context_fingerprint"):
        # Compatibility for checkpoints created before dependency tracking.
        retrieved = _retrieve(control, state["current_query"])
        changed = retrieved.context_fingerprint != state["memory_context_fingerprint"]
        if changed:
            return RunnerResult(
                answer="长期记忆在本次回答生成后发生变化，请重新提问以使用当前有效的记忆。",
                status="partial", stop_reason="memory_changed",
                memory_action=state.get("memory_action") or None,
                memory_owner_revision=retrieved.owner_revision,
                memory_context_fingerprint=retrieved.context_fingerprint,
            )
    if changed:
        # A final checkpoint may outlive a deletion/update/expiry during downtime.
        # Never hand its obsolete draft to the delivery transaction after recovery.
        return RunnerResult(
            answer="长期记忆在本次回答生成后发生变化，请重新提问以使用当前有效的记忆。",
            status="partial", stop_reason="memory_changed",
            memory_action=state.get("memory_action") or None,
        )
    repository = ToolResultRepository(control)
    observations = [{**attach_result_reference(repository.read(item["result_ref"]), item["result_ref"], control.row["run_id"]),
                     "name": item["name"]}
                    if item.get("result_ref") else item for item in state.get("tool_messages", [])]
    from app.services.memory_display import build_display_blocks
    display_blocks = build_display_blocks(
        observations, list(state.get("display_policies") or []),
    )
    return RunnerResult(answer=state.get("answer", ""), status=state.get("status", "error"),
                        stop_reason=state.get("stop_reason", "unknown"), tool_calls=state.get("tool_calls", []),
                        tool_messages=observations, display_blocks=display_blocks,
                        model_rounds=state.get("model_rounds", 0),
                        tool_count=state.get("tool_count", 0), error=state.get("error"),
                        compliance_reviewed=state.get("compliance_reviewed", False),
                        memory_action=state.get("memory_action") or None,
                        memory_owner_revision=state.get("memory_owner_revision"),
                        memory_context_fingerprint=state.get("memory_context_fingerprint") or None,
                        memory_dependencies=dependencies,
                        memory_context_used=state.get("memory_context_used", False),
                        duration_ms=round((perf_counter() - runner.started) * 1000))
