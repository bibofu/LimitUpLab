"""Project native graph events onto a small, replayable public protocol.

Node updates are provisional execution telemetry, never delivery receipts.
Candidate answer text travels only through the dedicated answer events and is
always replaceable; only RunDelivery's committed response can produce SSE
completed.
"""

import asyncio
from collections.abc import Mapping

from langgraph.config import get_stream_writer

PUBLIC_PROGRESS_EVENTS = frozenset({"progress", "status", "tool_start", "tool_end", "warning"})
ANSWER_EVENTS = frozenset({"answer_start", "answer_delta", "answer_reset"})
PUBLIC_STREAM_EVENTS = PUBLIC_PROGRESS_EVENTS | ANSWER_EVENTS
MAX_DELTA_CHARS = 4000
NODES = frozenset({"input_review", "agent", "tool", "memory"})
STAGES = {
    "preparing": "正在准备会话与研究数据",
    "planning": "正在理解问题并选择查询",
    "tools": "正在查询研究数据",
    "checking": "正在核验并保存研究结果",
    "answering": "正在整理完整回答",
}
TOOL_OUTCOMES = frozenset({"ok", "empty", "partial", "error", "success", "skipped"})


def emit_status(stage, _message=None):
    """Adapt the runner's fixed progress callback to LangGraph custom output."""
    if stage in STAGES:
        get_stream_writer()({"kind": "status", "stage": stage})


def emit_answer_event(kind, payload=None):
    """Send one candidate-answer event; the committed answer remains separate."""

    if kind not in ANSWER_EVENTS:
        raise ValueError("Unsupported answer event")
    get_stream_writer()({"kind": kind, **(payload or {})})


def emit_tool_event(kind, call, *, status=None):
    if kind not in {"tool_start", "tool_end"}:
        raise ValueError("Unsupported tool event")
    get_stream_writer()({"kind": kind, "node": "tool", "stage": "tools",
                         "tool_call_id": call.get("id"), "tool_name": call.get("name"), "status": status})


def _identifier(value):
    if not isinstance(value, str) or not 0 < len(value) <= 128:
        return None
    return value if all(character.isascii() and (character.isalnum() or character in "_-.")
                        for character in value) else None


class NativeStreamBridge:
    def __init__(self, control, tool_names=()):
        self.control = control
        self.tool_names = frozenset(tool_names)
        self.node_updates = 0

    async def publish(self, raw):
        if not isinstance(raw, Mapping):
            return
        kind, stage = raw.get("kind"), raw.get("stage")
        if kind in ANSWER_EVENTS:
            await self._publish_answer(kind, raw)
            return
        if kind not in PUBLIC_PROGRESS_EVENTS - {"progress"} or stage not in STAGES:
            return
        row = self.control.row
        payload = {"event_version": 1, "stage": stage, "message": STAGES[stage],
                   "run_id": row["run_id"], "thread_id": row["session_id"],
                   "recovery_count": int(row.get("recovery_count", 0))}
        if raw.get("node") in NODES:
            payload["node"] = raw["node"]
        if raw.get("phase") in {"node_output", "resume", "delivery_retry"}:
            payload["phase"] = raw["phase"]
            if raw["phase"] == "resume":
                payload["message"] = "正在从已保存的执行进度恢复原任务"
            elif raw["phase"] == "delivery_retry":
                payload["message"] = "已恢复执行结果，正在核验正式交付"
        if kind in {"tool_start", "tool_end"}:
            call_id = _identifier(raw.get("tool_call_id"))
            if call_id:
                payload["tool_call_id"] = call_id
            if raw.get("tool_name") in self.tool_names:
                payload["tool_name"] = raw["tool_name"]
            payload["message"] = "正在执行数据查询" if kind == "tool_start" else "数据查询已结束，正在核对结果"
            if raw.get("status") in TOOL_OUTCOMES:
                payload["status"] = raw["status"]
        if kind == "warning":
            payload["message"] = "部分查询未能完成，正在检查可用证据"
        await asyncio.to_thread(self.control.lifecycle.services.events.append,
            row["run_id"], kind, payload, execution=self.control.execution, admission=self.control.admission)
        notify = getattr(self.control, "notify", None)
        if notify:
            notify()

    async def _publish_answer(self, kind, raw):
        """Persist one candidate-answer frame with a whitelisted payload."""

        row = self.control.row
        payload = {"event_version": 1, "stage": "answering", "run_id": row["run_id"],
                   "thread_id": row["session_id"],
                   "recovery_count": int(row.get("recovery_count", 0))}
        if kind == "answer_start":
            if not isinstance(raw.get("revision"), int):
                return
            payload.update(revision=raw["revision"], provisional=True)
        elif kind == "answer_delta":
            delta = raw.get("delta")
            if (not isinstance(raw.get("offset"), int) or raw["offset"] < 0
                    or not isinstance(delta, str) or not delta or len(delta) > MAX_DELTA_CHARS):
                return
            payload.update(offset=raw["offset"], delta=delta)
        else:
            payload["message"] = str(raw.get("message") or "正在重新整理回答")[:200]
            if isinstance(raw.get("revision"), int):
                payload["revision"] = raw["revision"]
        await asyncio.to_thread(self.control.lifecycle.services.events.append,
            row["run_id"], kind, payload, execution=self.control.execution, admission=self.control.admission)
        notify = getattr(self.control, "notify", None)
        if notify:
            notify()

    async def consume(self, mode, chunk):
        if mode == "custom":
            await self.publish(chunk)
        elif mode == "updates" and isinstance(chunk, Mapping):
            for node, update in chunk.items():
                if node not in NODES or not isinstance(update, Mapping):
                    continue
                self.node_updates += 1
                stage = "checking" if update.get("done") else "tools" if update.get("pending") else "planning"
                await self.publish({"kind": "status", "stage": stage, "node": node, "phase": "node_output"})
