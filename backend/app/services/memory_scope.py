"""Keep revocable cross-session memory out of permanent session instructions."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _metadata(item) -> dict:
    return _value(item, "metadata", {}) or _value(item, "additional_kwargs", {}) or {}


def is_memory_control_message(item) -> bool:
    """Use trusted response provenance, never regex guesses from user text."""
    meta = _metadata(item)
    return meta.get("memory_scope") == "long_term" or bool(meta.get("memory_action"))


def filter_memory_control_turns(messages: list[Any]) -> list[Any]:
    """Keep only completed conversational intent, using persisted provenance.

    A canceled memory command can commit its write before the graph records its
    memory_action. Its terminal response still identifies the entire failed
    turn, so neither side may later authorize a preference through history.
    """
    def excluded(item):
        return is_memory_control_message(item) or (
            _value(item, "role") == "assistant" and (
                _metadata(item).get("task_status") in {"cancelled", "error", "refuse"}
                or _value(item, "status") == "error"
            )
        )

    controlled_runs = {_value(item, "run_id") for item in messages
                       if _value(item, "run_id") and excluded(item)}
    return [item for item in messages if not excluded(item)
            and _value(item, "run_id") not in controlled_runs]


def summary_source_messages(messages: list[Any]) -> list[Any]:
    """Assistant wording shaped by preferences must not become session memory."""
    return [item for item in filter_memory_control_turns(messages)
            if not (_value(item, "role") == "assistant" and _metadata(item).get("memory_context_used"))]


def _assistant_memory_changed(item, retrieval) -> bool:
    """A message is stale only when a memory it actually used changed."""

    meta = _metadata(item)
    dependencies = meta.get("memory_dependencies")
    checker = getattr(retrieval, "dependency_validator", None)
    if isinstance(dependencies, list) and callable(checker):
        try:
            return not bool(checker(dependencies))
        except Exception:
            # A failed dependency check must not publish stale preference-shaped prose.
            return True
    stored = meta.get("memory_context_fingerprint")
    return bool(stored and stored != retrieval.context_fingerprint)


def clean_conversation_for_memory_revision(conversation: dict, retrieval) -> dict:
    """Return an isolated view; the runtime may persist ONLY this cleaned state.

    New messages carry the exact memory dependencies used to generate them.
    Unrelated memory changes therefore leave their prose intact. Legacy messages
    without dependency metadata fall back to the old whole-snapshot behavior.
    """
    state = copy.deepcopy(conversation)
    recent = filter_memory_control_turns(state.get("recent_messages", []))
    has_dependency_metadata = any(
        _value(item, "role") == "assistant"
        and isinstance(_metadata(item).get("memory_dependencies"), list)
        for item in recent
    )
    previous = state.get("memory_context_fingerprint")
    removed = False
    kept = []
    for item in recent:
        if _value(item, "role") == "assistant":
            if has_dependency_metadata:
                if _assistant_memory_changed(item, retrieval):
                    removed = True
                    continue
            elif (_metadata(item).get("memory_context_fingerprint")
                  and _metadata(item)["memory_context_fingerprint"] != retrieval.context_fingerprint):
                removed = True
                continue
        kept.append(item)
    recent = kept
    changed = removed if has_dependency_metadata else (
        previous != retrieval.context_fingerprint
        and (previous is not None or retrieval.owner_revision > 0)
    )
    if changed:
        from app.models import ChatSessionMessage
        from app.services.conversation_summary import deterministic_summary
        from app.agents.structured_references import supported_session_constraints

        safe = [ChatSessionMessage.model_validate({"session_id": "graph-thread",
                "created_at": datetime.now(timezone.utc), **item})
                for item in summary_source_messages(recent) if isinstance(item, dict)]
        draft = deterministic_summary(None, safe) if safe else {}
        state["conversation_summary"] = draft.get("summary", "")
        state["research_context"] = {key: draft.get(key) for key in (
            "research_goal", "stock_symbols", "topics", "date_scope", "unresolved_questions") if key in draft}
        supported = supported_session_constraints(state.get("active_constraints"), state.get("active_references", []))
        state["active_constraints"] = supported or draft.get("constraints", [])
        state["summary_revision"] = int(state.get("summary_revision", 0)) + 1
        state["summary_generation_mode"] = "deterministic_memory_scope_rebuild"
        state["memory_scope_rebuilt"] = True
    else:
        state["memory_scope_rebuilt"] = False
    state["recent_messages"] = recent
    state["memory_owner_revision"] = retrieval.owner_revision
    state["memory_context_fingerprint"] = retrieval.context_fingerprint
    return state


def sanitize_working_messages_for_memory_revision(
    messages: list[BaseMessage], retrieval, previous_fingerprint: str | None,
    dependencies: list[dict[str, Any]] | None = None,
) -> list[BaseMessage]:
    """Preserve tool protocol/evidence while removing stale model prose on resume."""
    checker = getattr(retrieval, "dependency_validator", None)
    if isinstance(dependencies, list) and callable(checker):
        try:
            changed = not bool(checker(dependencies))
        except Exception:
            changed = True
    else:
        changed = (
            previous_fingerprint is not None
            and previous_fingerprint != retrieval.context_fingerprint
        )
    if not changed:
        return [item.model_copy(deep=True) for item in messages]
    result = []
    for message in messages:
        if isinstance(message, AIMessage):
            if message.tool_calls:
                result.append(message.model_copy(update={"content": ""}, deep=True))
            # Completed or intermediate old prose is regenerated from the query
            # and durable tool evidence under the latest preference snapshot.
        elif isinstance(message, ToolMessage):
            result.append(message.model_copy(deep=True))
        else:
            result.append(message.model_copy(deep=True))
    return result
