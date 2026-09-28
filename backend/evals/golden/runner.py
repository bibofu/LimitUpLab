"""Real-model evaluation with persistent isolated sessions and a frozen market world."""

from contextlib import ExitStack
from copy import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from app.agents.chat import answer_first_board_chat
from app.agents.react_runtime.lifecycle import CURRENT_CONTROL, Journal, RunControl
from app.models import AgentChatRequest, AgentChatResponse, ChatSessionMemory, ChatSessionMessage
from app.repositories.chat_memory_repository import SQLiteChatMemoryRepository
from app.repositories.chat_session_repository import SQLiteChatSessionRepository
from app.services.llm_provider import capture_llm_usage
from app.services.session_memory import prepare_session_context
from evals.golden.contracts import Check, verdict
from evals.golden.judge import judge_turn


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, maximum, used=0, on_take=None):
        self.maximum, self.used = maximum, used
        self.denied = 0
        self.on_take = on_take

    def take(self):
        if self.used >= self.maximum:
            self.denied += 1
            raise BudgetExceeded("Evaluation model-call budget exhausted")
        self.used += 1
        # Reserve durably before making a billable request, including interrupted trials.
        if self.on_take is not None:
            self.on_take(self.used)


def without_sdk_retries(provider):
    """Copy the evaluation provider so memory/text calls cannot retry invisibly."""
    model = getattr(provider, "chat_model", None)
    if model is None:
        return provider  # Scripted protocol fixtures have no network client.
    options = {"max_retries": 0}
    for root_name, client_name in (("root_client", "client"), ("root_async_client", "async_client")):
        root = getattr(model, root_name, None)
        if root is not None:
            configured = root.with_options(max_retries=0)
            options.update({root_name: configured, client_name: configured.chat.completions})
    configured = copy(provider)
    configured.chat_model = model.model_copy(update=options)
    return configured


class BudgetedProvider:
    """Budget every Agent, memory and judge call without changing model messages."""

    def __init__(self, provider, budget):
        self.provider, self.budget = provider, budget
        self.model = getattr(provider, "model", type(provider).__name__)

    def generate_messages(self, *args, **kwargs):
        self.budget.take()
        return self.provider.generate_messages(*args, **kwargs)

    def generate_function_call(self, *args, **kwargs):
        self.budget.take()
        return self.provider.generate_function_call(*args, **kwargs)

    def generate(self, *args, **kwargs):
        self.budget.take()
        return self.provider.generate(*args, **kwargs)


def usage_payload(tracker):
    result = asdict(tracker)
    result["total_tokens"] = tracker.total_tokens if tracker.token_usage_complete else None
    result["token_usage_complete"] = tracker.token_usage_complete
    return result


def run_case(case, *, trial, directory, provider, judge_provider=None):
    # Import late so validation does not require model credentials or construct registries.
    from evals.golden.grading import grade_turn, visible_drafts
    from evals.golden.world import FrozenRegistry

    started = perf_counter()
    budget = getattr(provider, "budget", None)
    denied_before = budget.denied if budget else 0
    work = Path(directory) / "sessions" / f"{case.id}-t{trial}-{uuid4().hex[:8]}"
    work.mkdir(parents=True)
    database = work / "session.sqlite"
    owner = "golden_owner"
    chats = SQLiteChatSessionRepository(database)
    memories = SQLiteChatMemoryRepository(database)
    journal = Journal(database)
    anchor = datetime.fromisoformat(case.clock).date()
    results, initialized = [], set()
    registry = FrozenRegistry(case)
    error = None
    with ExitStack() as stack:
        # Each invocation is sequential and isolated; never run this harness in a live server.
        for target in ("app.agents.query_contract.current_query_reference_date",
                       "app.agents.react_runtime.runtime.current_query_reference_date",
                       "app.agents.react_runtime.tools.current_query_reference_date"):
            stack.enter_context(patch(target, return_value=anchor))
        stack.enter_context(patch.dict("os.environ", {"LIMITUPLAB_DATABASE_PATH": str(database),
                                                    "LIMITUPLAB_SESSION_MEMORY_ENABLED": "true",
                                                    "LIMITUPLAB_SESSION_MEMORY_REFRESH_MESSAGES": "8"}))
        for index, turn in enumerate(case.turns):
            session_id = f"golden-{case.id}-t{trial}-{turn.session}"
            session = chats.ensure_session(session_id, owner_id=owner)
            if turn.session not in initialized:
                initialized.add(turn.session)
                if turn.session == "main":
                    seed_time = datetime.now(timezone.utc) - timedelta(minutes=len(case.seed_messages) + 1)
                    for offset, seed in enumerate(case.seed_messages):
                        chats.append_message(ChatSessionMessage(
                            message_id=f"seed-{offset}", session_id=session_id, role=seed.role,
                            content=seed.content, created_at=seed_time + timedelta(minutes=offset),
                        ), owner_id=owner)
                    if case.memory_seed is not None:
                        now = datetime.now(timezone.utc)
                        memory = ChatSessionMemory.model_validate({**case.memory_seed, "session_id": session_id,
                            "owner_id": owner, "memory_version": "golden-seed-v1", "created_at": now, "updated_at": now})
                        memories.save_memory(memory)
                session = chats.get_session(session_id, owner_id=owner)
            request = AgentChatRequest(session_id=session_id, message_id=f"turn-{index}", message=turn.user,
                                       trade_date=turn.page_date, symbol=turn.page_symbol)
            row, _ = journal.create(request, owner)
            token = CURRENT_CONTROL.set(RunControl(journal, row))
            events = []
            def event(name, payload):
                events.append({"event": name, "payload": payload})
                journal.event(row["run_id"], name, payload)
            turn_started = perf_counter()
            agent_usage = None
            try:
                chats.append_message(ChatSessionMessage(message_id=f"user-{index}", session_id=session_id,
                    role="user", content=turn.user, run_id=row["run_id"], created_at=datetime.now(timezone.utc)), owner_id=owner)
                with capture_llm_usage() as agent_usage:
                    context, memory = prepare_session_context(session_id=session_id, owner_id=owner,
                        messages=session.messages, repository=memories, llm_provider=provider)
                    response = answer_first_board_chat(request=request, events=registry.events,
                        conversation_messages=context, session_memory=memory, llm_provider=provider,
                        tool_registry=registry, answer_event_callback=event)
                response = AgentChatResponse.model_validate(journal.finish(row["run_id"], owner, response.model_dump(mode="json")))
            except Exception as exc:
                error = type(exc).__name__
                results.append({"index": index, "user": turn.user, "checks": [Check(name="execution", passed=False,
                    detail=error).model_dump()], "verdict": "fail", "events": events,
                    "agent_usage": usage_payload(agent_usage) if agent_usage else {},
                    "duration_seconds": round(perf_counter() - turn_started, 3)})
                break
            finally:
                CURRENT_CONTROL.reset(token)
            judgements = safety = judge_error = None
            judge_usage = None
            if judge_provider is not None:
                tracker = None
                try:
                    with capture_llm_usage() as tracker:
                        judgements, safety = judge_turn(judge_provider, user=turn.user,
                            expectations=turn.expect.semantic_checks, response=response, drafts=visible_drafts(events))
                except Exception as exc:
                    judge_error = type(exc).__name__
                finally:
                    judge_usage = usage_payload(tracker) if tracker else None
            checks = grade_turn(turn.expect, response, events=events, judgements=judgements)
            # Streaming safety is evaluated independently of the production compliance gate.
            checks.append(Check(name="visible_answer_safety", passed=safety["passed"] if safety else None,
                detail=safety["reason"] if safety else "Independent safety review pending"))
            results.append({"index": index, "user": turn.user, "session": turn.session,
                "expected": turn.expect.model_dump(), "response": response.model_dump(mode="json"),
                "events": events, "judgements": judgements, "safety_judgement": safety, "judge_error": judge_error,
                "memory_before": memory.model_dump(mode="json") if memory else None,
                "context_message_count": len(context), "checks": [check.model_dump() for check in checks],
                "verdict": verdict(checks), "agent_usage": usage_payload(agent_usage), "judge_usage": judge_usage,
                "duration_seconds": round(perf_counter() - turn_started, 3)})
            if budget and budget.denied > denied_before:
                break
    trial_verdict = verdict([Check(name=f"turn_{item['index']}", passed=(True if item["verdict"] == "pass"
        else False if item["verdict"] == "fail" else None)) for item in results])
    unsupported = sorted(set(registry.unsupported_tools))
    if unsupported:
        trial_verdict = "harness_error"
        error = "Fixture coverage gap: " + ", ".join(unsupported)
    budget_exhausted = bool(budget and budget.denied > denied_before)
    if budget_exhausted:
        trial_verdict = "harness_error"
        error = "Evaluation model-call budget exhausted; this is not an Agent capability score"
    return {"case_id": case.id, "trial": trial, "category": case.category, "family": case.family,
        "split": case.split, "tags": case.tags, "definition": case.model_dump(), "verdict": trial_verdict,
        "turns": results, "error": error, "unsupported_tools": unsupported,
        "completed": len(results) == len(case.turns) and not budget_exhausted,
        "planned_turns": len(case.turns), "session_directory": str(work),
        "stop_reason": "model_call_budget" if budget_exhausted else "execution_error" if error else "finished",
        "duration_seconds": round(perf_counter() - started, 3)}
