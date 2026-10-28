"""Bounded, immutable answer snapshots used by the review queue."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 20000


@dataclass(frozen=True)
class RunSnapshot:
    """The answer facts frozen when a case is first created."""

    run_id: str
    session_id: str
    owner_id: str
    question: str
    answer: str
    intent: str | None
    task_status: str | None
    trace_url: str | None
    evidence: dict[str, Any] = field(default_factory=dict)


def load_run_snapshot(connection, run_id: str) -> RunSnapshot | None:
    """Read one completed run without loading full tool payloads into the case."""

    row = connection.execute(
        """
        SELECT run_id, session_id, owner_id, request_json, response_json, runtime_version
        FROM react_runs
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None or not row["response_json"]:
        return None
    try:
        request = json.loads(row["request_json"] or "{}")
        response = json.loads(row["response_json"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    raw_question = str(request.get("message") or "")
    raw_answer = str(response.get("answer") or "")
    usage = connection.execute(
        """
        SELECT model, prompt_tokens, completion_tokens, total_tokens,
               token_usage_complete, duration_ms
        FROM agent_usage_events
        WHERE run_id = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    tool_results = response.get("tool_results")
    evidence = {
        "task_status": response.get("task_status"),
        "stop_reason": response.get("stop_reason"),
        "intent": response.get("intent"),
        "tool_calls": response.get("tool_calls") or [],
        "tool_result_count": len(tool_results) if isinstance(tool_results, list) else 0,
        "warnings": response.get("warnings") or [],
        "trace_url": response.get("trace_url"),
        "runtime_version": row["runtime_version"],
        "build_revision": os.getenv("LIMITUPLAB_BUILD_REVISION", "").strip() or None,
        "requested_trade_date": request.get("trade_date"),
        "symbol": request.get("symbol"),
        "question_truncated": len(raw_question) > MAX_QUESTION_CHARS,
        "answer_truncated": len(raw_answer) > MAX_ANSWER_CHARS,
        "source": "react_runs.response_json",
    }
    if usage is not None:
        evidence["model"] = usage["model"]
        evidence["prompt_tokens"] = usage["prompt_tokens"]
        evidence["completion_tokens"] = usage["completion_tokens"]
        evidence["total_tokens"] = usage["total_tokens"]
        evidence["token_usage_complete"] = bool(usage["token_usage_complete"])
        evidence["duration_ms"] = usage["duration_ms"]
    return RunSnapshot(
        run_id=row["run_id"],
        session_id=row["session_id"],
        owner_id=row["owner_id"],
        question=raw_question[:MAX_QUESTION_CHARS],
        answer=raw_answer[:MAX_ANSWER_CHARS],
        intent=response.get("intent"),
        task_status=response.get("task_status"),
        trace_url=response.get("trace_url"),
        evidence=evidence,
    )
