"""Token-triggered rolling summaries computed for one exclusively owned thread.

This module performs no database writes. The graph owns the returned state; its
single-writer lease/CAS must guard persistence of ``revision``. Callers provide a
chronological window containing the previous committed cursor and new messages.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from app.agents.context_builder import estimate_tokens
from app.models import ChatSessionMessage
from app.services.conversation_summary import (
    SUMMARY_SCHEMA_VERSION, ConversationSummary, build_summary, deterministic_summary,
    generate_summary, successful_messages,
)
from app.services.memory_scope import filter_memory_control_turns, summary_source_messages


class MemoryRevisionConflict(ValueError):
    """A caller tried to compute an update from another conversation revision."""


class MemoryCursorError(ValueError):
    """The supplied window cannot prove its ordering after the durable cursor."""


def _record(item: Any) -> dict[str, Any]:
    record = item.model_dump(mode="json") if hasattr(item, "model_dump") else copy.deepcopy(item)
    if not isinstance(record, dict) or not record.get("message_id"):
        raise MemoryCursorError("Committed messages need stable message_id values")
    return record


def _model(record: dict[str, Any]) -> ChatSessionMessage:
    return ChatSessionMessage.model_validate({
        "session_id": "graph-thread", "created_at": datetime.now(timezone.utc), **record,
    })


def _groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for item in messages:
        if item.get("role") == "user" or not groups:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def _existing_summary(state: dict) -> ConversationSummary | None:
    summary = state.get("conversation_summary", "")
    research = state.get("research_context", {})
    constraints = state.get("active_constraints", [])
    if not summary and not research and not constraints:
        return None
    if isinstance(constraints, dict):
        constraints = [f"{key}: {value}" for key, value in constraints.items()]
    return ConversationSummary(
        summary=str(summary), research_goal=str(research.get("research_goal") or ""),
        stock_symbols=research.get("stock_symbols", []), topics=research.get("topics", []),
        date_scope=research.get("date_scope"), constraints=constraints,
        unresolved_questions=research.get("unresolved_questions", []),
    )


def compress_conversation(
    conversation: dict[str, Any], committed_messages: list[Any], provider=None, *,
    expected_revision: int | None = None, summary_trigger_tokens: int = 6000,
    recent_window_tokens: int = 2400,
) -> dict[str, Any]:
    """Append committed turns once, then summarize older whole turns if needed.

    No cursor is guessed when an older window was supplied. ``provider=None``
    uses the existing deterministic safe summary; pass a provider to request the
    existing structured LLM summarizer with its explicit fallback.
    """
    revision = int(conversation.get("revision", 0))
    if expected_revision is not None and revision != expected_revision:
        raise MemoryRevisionConflict("Conversation revision changed")
    if recent_window_tokens < 1 or summary_trigger_tokens <= recent_window_tokens:
        raise ValueError("Summary trigger must exceed the positive recent window budget")
    state = copy.deepcopy(conversation)
    incoming = [_record(item) for item in committed_messages]
    ids = [item["message_id"] for item in incoming]
    if len(ids) != len(set(ids)):
        raise MemoryCursorError("Committed message window contains duplicate ids")
    cursor = state.get("last_committed_message_id")
    if cursor:
        if cursor not in ids:
            raise MemoryCursorError("Committed window does not contain the previous cursor")
        incoming = incoming[ids.index(cursor) + 1:]
    if not incoming:
        return state
    recent = list(state.get("recent_messages", []))
    known = {item["message_id"] for item in recent}
    if any(item["message_id"] in known for item in incoming):
        raise MemoryCursorError("Committed window replays a message before the previous cursor")
    # Inspect complete turns before dropping failed assistant rows, including
    # a user already in recent when its terminal response arrives later.
    candidates = filter_memory_control_turns([*recent, *incoming])
    successful = successful_messages([_model(item) for item in candidates])
    eligible_ids = {item.message_id for item in successful}
    recent = [item for item in candidates if item["message_id"] in eligible_ids]
    state.update(recent_messages=recent, last_committed_message_id=incoming[-1]["message_id"],
                 revision=revision + 1, summary_triggered=False)
    context_size = sum(estimate_tokens(item.get("content", "")) for item in recent)
    context_size += estimate_tokens(state.get("conversation_summary", ""))
    state["conversation_token_estimate"] = context_size
    if context_size <= summary_trigger_tokens:
        return state
    groups = _groups(recent)
    keep_count, used = 0, 0
    for group in reversed(groups):
        cost = sum(estimate_tokens(item.get("content", "")) for item in group)
        if keep_count and used + cost > recent_window_tokens:
            break
        keep_count += 1
        used += cost
    older = [item for group in groups[:-keep_count] for item in group]
    if not older:
        state["summary_deferred_reason"] = "latest_turn_exceeds_recent_window"
        return state
    models = [_model(item) for item in summary_source_messages(older)]
    if not models:
        # Advance only the compression cursor; no revoked or preference-shaped
        # assistant text is allowed into the next summary.
        state.update(recent_messages=[item for group in groups[-keep_count:] for item in group],
                     summary_cursor=older[-1]["message_id"], summary_triggered=False,
                     summary_deferred_reason="no_session_scoped_summary_sources")
        return state
    existing = _existing_summary(state)
    try:
        if provider is None:
            draft, mode, model = deterministic_summary(existing, models), "deterministic", None
        else:
            draft, mode, model = generate_summary(existing=existing, messages=models, llm_provider=provider)
    except Exception as error:  # summary is optional; facts remain in the chat ledger
        draft, mode, model = deterministic_summary(existing, models), "deterministic", None
        state["summary_error"] = type(error).__name__
    summary = build_summary(existing=existing, draft=draft, messages=models)
    state.update(
        conversation_summary=summary.summary,
        research_context={"research_goal": summary.research_goal, "stock_symbols": summary.stock_symbols,
                          "topics": summary.topics, "date_scope": summary.date_scope,
                          "unresolved_questions": summary.unresolved_questions},
        recent_messages=[item for group in groups[-keep_count:] for item in group],
        summary_cursor=older[-1]["message_id"],
        summary_revision=int(state.get("summary_revision", 0)) + 1,
        summarized_message_count=state.get("summarized_message_count", 0) + len(models),
        summary_generation_mode=mode, summary_model=model,
        summary_schema_version=SUMMARY_SCHEMA_VERSION, summary_triggered=True,
    )
    if not isinstance(state.get("active_constraints"), dict):
        state["active_constraints"] = summary.constraints
    state["conversation_token_estimate"] = used + estimate_tokens(summary.summary)
    state.pop("summary_deferred_reason", None)
    return state
