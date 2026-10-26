"""One-time recovery of summary lineage from the owned, delivered chat ledger.

No writes occur here. Only the normal fenced graph checkpoint may publish the
returned replacement; a failed or timed-out replay leaves the old state intact.
"""

from __future__ import annotations

import copy
import json
from contextlib import closing
from time import perf_counter

from app.agents.conversation_memory import MemoryCursorError, compress_conversation
from app.agents.runtime_errors import RecoverableRunError
from app.agents.structured_references import supported_session_constraints
from app.database import connect_readonly
from app.services.memory_scope import (
    SESSION_SUMMARY_SCOPE_VERSION, filter_memory_control_turns,
    has_session_summary_lineage,
)


def needs_summary_recovery(conversation: dict) -> bool:
    return not has_session_summary_lineage(conversation) and bool(
        conversation.get("conversation_summary") or conversation.get("summary_cursor"))


def _check_budget(deadline: float) -> float:
    remaining = deadline - perf_counter() - 15  # reserve time for the current answer
    if remaining <= 0:
        raise RecoverableRunError("历史会话摘要恢复超出本轮时间预算，请重试原任务。")
    return remaining


class _BudgetedProvider:
    def __init__(self, provider, deadline):
        self.provider, self.deadline = provider, deadline

    def _call(self, method, *args, **kwargs):
        remaining = _check_budget(self.deadline)
        provider = copy.copy(self.provider)
        if hasattr(provider, "timeout_seconds"):
            provider.timeout_seconds = min(provider.timeout_seconds, remaining)
        return getattr(provider, method)(*args, **kwargs)

    def generate_function_call(self, *args, **kwargs):
        return self._call("generate_function_call", *args, **kwargs)

    def generate(self, *args, **kwargs):
        return self._call("generate", *args, **kwargs)


def _committed_ledger(database_url, *, session_id, owner_id, current_run_id, deadline):
    """Keyset pages share one read-only snapshot; never the UI's latest-100 view."""
    messages, after = [], None
    with closing(connect_readonly(database_url)) as db:
        owned = db.execute("""SELECT 1 FROM chat_sessions
            WHERE session_id=? AND owner_id=? AND archived_at IS NULL""", (session_id, owner_id)).fetchone()
        if owned is None:
            raise MemoryCursorError("Summary source session is unavailable for this owner")
        while True:
            _check_budget(deadline)
            paging = " AND (m.created_at,m.legacy_order) > (?,?)" if after else ""
            rows = db.execute("""SELECT m.* FROM chat_messages AS m
                LEFT JOIN react_runs AS r ON r.run_id=m.run_id
                WHERE m.session_id=? AND (m.run_id IS NULL OR m.run_id<>?)
                  AND (r.run_id IS NULL OR (r.owner_id=? AND r.session_id=? AND r.response_json IS NOT NULL))
                """ + paging + " ORDER BY m.created_at,m.legacy_order LIMIT 200",
                (session_id, current_run_id, owner_id, session_id, *(after or ()))).fetchall()
            if not rows:
                break
            for row in rows:
                record = dict(row)
                record["metadata"] = json.loads(record.pop("metadata_json") or "{}")
                messages.append(record)
            after = (rows[-1]["created_at"], rows[-1]["legacy_order"])
    # Old imports have no react_runs row; require a complete user/assistant turn
    # in the ledger before accepting even their user text as committed context.
    completed, group = [], []
    for message in messages:
        if message["role"] == "user" and group:
            if any(item["role"] == "assistant" for item in group):
                completed.extend(group)
            group = []
        group.append(message)
    if any(item["role"] == "assistant" for item in group):
        completed.extend(group)
    return completed


def _session_users(ledger):
    groups = []
    for item in ledger:
        if item["role"] == "user" or not groups:
            groups.append([])
        groups[-1].append(item)
    # Exclude the whole turn even for old imports with no stable run_id.
    return [item for group in groups if len(filter_memory_control_turns(group)) == len(group)
            for item in group if item["role"] == "user" and item.get("status") == "success"
            and item.get("content", "").strip()]


def recover_session_summary(conversation, *, database_url, session_id, owner_id,
                            current_run_id, provider, deadline):
    """Replay only safe user facts, without using any unverified old prose."""
    try:
        ledger = _committed_ledger(database_url, session_id=session_id, owner_id=owner_id,
                                   current_run_id=current_run_id, deadline=deadline)
        ids = {item["message_id"] for item in ledger}
        for field in ("summary_cursor", "last_committed_message_id"):
            if conversation.get(field) and conversation[field] not in ids:
                raise MemoryCursorError("Summary source cursor is absent from the committed ledger")
        if not ledger:
            raise MemoryCursorError("Summary has no committed source ledger")
        # Legacy assistant prose has incomplete preference provenance. Keep its
        # terminal metadata for whole-turn exclusion, but never summarize it.
        sources = _session_users(ledger)
        rebuilt = {key: copy.deepcopy(conversation[key]) for key in (
            "revision", "summary_revision", "active_references", "imported_from") if key in conversation}
        rebuilt["active_constraints"] = supported_session_constraints(
            conversation.get("active_constraints"), rebuilt.get("active_references", []))
        bounded = _BudgetedProvider(provider, deadline) if provider is not None else None
        # At most 24 complete historical user turns enter any replay step. The
        # compressor retains its bounded recent window between steps.
        for offset in range(0, len(sources), 24):
            _check_budget(deadline)
            batch = sources[offset:offset + 24]
            if offset:
                batch = [sources[offset - 1], *batch]
            rebuilt = compress_conversation(rebuilt, batch, provider=bounded, require_summary_success=True)
            _check_budget(deadline)
        rebuilt.update(summary_scope_version=SESSION_SUMMARY_SCOPE_VERSION,
                       last_committed_message_id=ledger[-1]["message_id"],
                       summary_recovered_from_ledger=True,
                       summary_recovery_source_count=len(sources))
        return rebuilt
    except RecoverableRunError:
        raise
    except Exception as error:
        raise RecoverableRunError("历史会话摘要来源恢复失败，已保留原状态，请重试或检查已提交消息账本。") from error
