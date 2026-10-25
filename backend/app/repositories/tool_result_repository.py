"""Immutable, run-owned observations; never restore from mutable market caches."""

import hashlib
import json
from datetime import datetime, timezone

from app.agents.run_lifecycle import RunConflict
from app.services.agent_tracing import trace_stage


class ToolResultRepository:
    def __init__(self, control):
        self.control = control
        self.store = control.journal
        self.run_id = control.row["run_id"]

    def read(self, call_id):
        with self.store.connection() as db:
            row = db.execute("""SELECT payload_json FROM agent_tool_results
                WHERE run_id=? AND call_id=? AND status='complete'""", (self.run_id, call_id)).fetchone()
        if row is None:
            raise RuntimeError("Persisted tool observation is missing")
        return json.loads(row["payload_json"])

    def execute(self, call, operation):
        signature = hashlib.sha256(json.dumps(
            [call["name"], call.get("args", {})], sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest()
        metadata = {"run_id": self.run_id, "thread_id": self.control.row["session_id"],
                    "tool_call_id": call["id"], "tool_name": call["name"]}
        with trace_stage("tool_receipt", {**metadata, "operation": "claim_or_replay"}) as details:
            with self.store.connection() as db:
                self.store.begin_execution_write(db, self.run_id)
                old = db.execute("SELECT * FROM agent_tool_results WHERE run_id=? AND call_id=?",
                                 (self.run_id, call["id"])).fetchone()
                details["receipt_reused"] = False
                if old:
                    if old["signature"] != signature:
                        raise RunConflict("Persisted tool call arguments changed")
                    if old["payload_json"]:
                        details["receipt_reused"] = True
                        return json.loads(old["payload_json"])
                    # The process may have died after the external call. A missing
                    # receipt is uncertainty, not permission to repeat side effects.
                    return {"status": "error", "error_type": "UncertainExecution",
                            "error": "上次工具执行结果未能持久化，无法确认是否完成。请重新发起查询。",
                            "data_missing": ["tool_execution_receipt"]}
                db.execute("""INSERT INTO agent_tool_results
                    (run_id,call_id,signature,status,created_at) VALUES(?,?,?,'started',?)""",
                    (self.run_id, call["id"], signature, datetime.now(timezone.utc).isoformat()))
        value = operation()
        # Validation/normalization of observations occurs before this boundary.
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with trace_stage("tool_receipt", {**metadata, "operation": "persist"}):
            with self.store.connection() as db:
                self.store.begin_execution_write(db, self.run_id)
                db.execute("""UPDATE agent_tool_results SET status='complete',payload_json=?
                    WHERE run_id=? AND call_id=? AND status='started'""",
                    (serialized, self.run_id, call["id"]))
        return value
