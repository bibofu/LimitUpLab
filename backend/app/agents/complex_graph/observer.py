"""Normalize tool observations into constrained entity sets."""

from __future__ import annotations

from typing import Any

from app.models import AgentToolTrace

from .models import EntityRef


def result_state(trace: AgentToolTrace) -> str:
    if trace.result is not None:
        return trace.result.status
    if trace.status == "error":
        return "error"
    return "empty" if not trace.output else "ok"


def symbols_from_trace(trace: AgentToolTrace, *, limit: int = 20) -> list[EntityRef]:
    rows: Any = []
    if trace.name == "first_board_ratings":
        rows = trace.output.get("top_candidates", [])
    elif trace.name == "limit_up_events":
        rows = trace.output.get("events", [])
    elif trace.name == "hot_stock_ranking":
        rows = trace.output.get("items", [])
    if not isinstance(rows, list):
        return []
    return [
        {"symbol": str(row["symbol"]), "name": row.get("name"), "source_steps": []}
        for row in rows[:limit]
        if isinstance(row, dict) and row.get("symbol")
    ]


def observation_payload(traces: list[AgentToolTrace]) -> list[dict[str, Any]]:
    return [
        {"tool": trace.name, "tool_args": trace.input, "result_state": result_state(trace), "summary": trace.summary}
        for trace in traces
    ]
