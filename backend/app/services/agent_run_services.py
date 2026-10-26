"""Business run registration, fenced delivery and replayable progress events.

These services persist business records only. LangGraph owns execution state;
neither the registry nor the event stream is an execution checkpoint.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.database import begin_write, connect, is_integrity_error
from app.models import AgentChatResponse
from app.agents.runtime_errors import RunPauseRequested
from app.services.runtime_coordination import LeaseLost, LeaseStore


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class RunConflict(ValueError):
    """The request conflicts with an existing request or active conversation."""


class RunStorage:
    def __init__(self, database_url=None):
        self.leases = LeaseStore(database_url)
        self.database_url = self.leases.database_url

    @contextmanager
    def connection(self):
        db = connect(self.database_url)
        try:
            with db:
                yield db
        finally:
            db.close()


class RunCoordinator:
    """Coordinate workers without using process-local worker bookkeeping."""

    def __init__(self, storage):
        self.storage = storage

    def claim(self, run_id):
        return self.storage.leases.claim("react:" + run_id)

    def executing(self, run_id):
        return self.storage.leases.live("react:" + run_id)

    def assert_current(self, db, run_id, execution=None, admission=None):
        self.storage.leases.assert_current(db, "react:" + run_id, execution)
        if admission is not None and hasattr(admission, "assert_current"):
            admission.assert_current(db)


class RunRegistry:
    """Own immutable request identity and public run lifecycle records."""

    def __init__(self, storage, coordinator):
        self.storage, self.coordinator = storage, coordinator

    def find_request(self, request, owner_id):
        """Resolve idempotent replay without creating a session or run."""
        if not request.message_id:
            return None
        with self.storage.connection() as db:
            row = db.execute(
                "SELECT * FROM react_runs WHERE owner_id=? AND session_id=? AND message_key=?",
                (owner_id, request.session_id, request.message_id),
            ).fetchone()
        if row is None:
            return None
        fingerprint = hashlib.sha256(encode(request.model_dump(mode="json")).encode()).hexdigest()
        if row["request_hash"] != fingerprint:
            raise RunConflict("同一 message_id 不能用于不同请求")
        return dict(row)

    def create(self, request, owner_id, *, runtime_version="durable-v1"):
        payload = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
        key = request.message_id or uuid4().hex
        with self.storage.connection() as db:
            begin_write(db)
            existing = db.execute(
                "SELECT * FROM react_runs WHERE owner_id=? AND session_id=? AND message_key=?",
                (owner_id, request.session_id, key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != fingerprint:
                    raise RunConflict("同一 message_id 不能用于不同请求")
                return dict(existing), False
            identity = db.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id=? AND owner_id=?",
                (request.session_id, owner_id),
            ).fetchone()
            if identity is None:
                raise KeyError("Session not found")
            run_id = "run_" + uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            try:
                db.execute(
                    """INSERT INTO react_runs
                    (run_id,owner_id,session_id,message_key,request_json,request_hash,started_at,
                     thread_id,runtime_version,run_status,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, owner_id, request.session_id, key, encode(payload), fingerprint,
                     now, request.session_id, runtime_version, "pending", now),
                )
            except Exception as error:
                if not is_integrity_error(error):
                    raise
                raise RunConflict("该会话已有任务执行中，请等待或取消后再发送") from error
            return dict(db.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()), True

    @staticmethod
    def owned_row(db, run_id, owner_id):
        row = db.execute(
            "SELECT * FROM react_runs WHERE run_id=? AND owner_id=?", (run_id, owner_id),
        ).fetchone()
        if row is None:
            raise KeyError("Run not found")
        return dict(row)

    def get(self, run_id, owner_id):
        with self.storage.connection() as db:
            return self.owned_row(db, run_id, owner_id)

    def delivered_response(self, run_id, owner_id):
        """Read the authoritative result before resuming or redelivering a run."""
        row = self.get(run_id, owner_id)
        return json.loads(row["response_json"]) if row["response_json"] else None

    def mark_running(self, run_id, owner_id, *, execution, admission=None):
        with self.storage.connection() as db:
            begin_write(db)
            row = self.owned_row(db, run_id, owner_id)
            self.coordinator.assert_current(db, run_id, execution, admission)
            if not row["active"] or row["response_json"]:
                raise RunConflict("Run is already terminal")
            if not row["cancelled"] and row["run_status"] in {"pausing", "paused"}:
                raise RunPauseRequested(row)
            recovery = int(row["run_status"] not in {"pending", "resuming"})
            db.execute("""UPDATE react_runs SET run_status='running', updated_at=?,
                recovery_count=recovery_count+? WHERE run_id=? AND owner_id=?""",
                (datetime.now(timezone.utc).isoformat(), recovery, run_id, owner_id))
            return self.owned_row(db, run_id, owner_id)

    def mark_interrupted(self, run_id, owner_id, *, execution, admission=None):
        """Record resumability without replacing the last graph checkpoint."""
        with self.storage.connection() as db:
            begin_write(db)
            row = self.owned_row(db, run_id, owner_id)
            if row["response_json"] or not row["active"]:
                return False
            self.coordinator.assert_current(db, run_id, execution, admission)
            if row["run_status"] in {"pausing", "paused", "resuming"} and not row["cancelled"]:
                return False
            db.execute("""UPDATE react_runs SET run_status='interrupted', updated_at=?
                WHERE run_id=? AND owner_id=?""",
                (datetime.now(timezone.utc).isoformat(), run_id, owner_id))
            return True

    def recoverable_runs(self, *, limit=20, max_recoveries=3):
        """Find bounded durable work with no live execution owner."""
        with self.storage.connection() as db:
            return [dict(row) for row in db.execute("""
                SELECT run.* FROM react_runs AS run
                LEFT JOIN runtime_leases AS lease ON lease.name='react:' || run.run_id
                WHERE run.active=1 AND run.response_json IS NULL
                  AND run.runtime_version='durable-v1'
                  AND (run.run_status!='paused' OR run.cancelled=1)
                  AND (run.recovery_count<? OR run.cancelled=1 OR run.run_status IN ('pausing','resuming'))
                  AND (lease.expires_at IS NULL OR lease.expires_at<=EXTRACT(EPOCH FROM clock_timestamp()))
                ORDER BY run.updated_at,run.started_at LIMIT ?
            """, (max_recoveries, limit))]

    def discard_unstarted(self, run_id, owner_id, execution=None):
        with self.storage.connection() as db:
            begin_write(db)
            try:
                self.coordinator.assert_current(db, run_id, execution)
            except LeaseLost:
                return False
            deleted = db.execute("""
                DELETE FROM react_runs
                WHERE run_id=? AND owner_id=? AND active=1 AND cancelled=0
                  AND control_revision=0
                  AND checkpoint_json IS NULL AND response_json IS NULL
                  AND NOT EXISTS (SELECT 1 FROM react_events WHERE run_id=react_runs.run_id)
                  AND NOT EXISTS (SELECT 1 FROM react_calls WHERE run_id=react_runs.run_id)
                  AND NOT EXISTS (SELECT 1 FROM chat_messages WHERE run_id=react_runs.run_id)
            """, (run_id, owner_id))
            return deleted.rowcount == 1

    def cancel(self, run_id, owner_id):
        with self.storage.connection() as db:
            begin_write(db)
            self.owned_row(db, run_id, owner_id)
            db.execute(
                """UPDATE react_runs SET cancelled=1,control_revision=control_revision+1,updated_at=?
                    WHERE run_id=? AND owner_id=? AND active=1 AND cancelled=0""",
                (datetime.now(timezone.utc).isoformat(), run_id, owner_id),
            )


class RunEventStore:
    """Persist replay cursors independently of graph persistence."""

    def __init__(self, storage, coordinator):
        self.storage, self.coordinator = storage, coordinator

    def append(self, run_id, event, payload, *, execution=None, admission=None):
        with self.storage.connection() as db:
            begin_write(db)
            self.coordinator.assert_current(db, run_id, execution, admission)
            db.execute(
                "INSERT INTO react_events(run_id,event,payload_json) VALUES(?,?,?)",
                (run_id, event, encode(payload)),
            )

    def list(self, run_id, owner_id, after=0):
        with self.storage.connection() as db:
            RunRegistry.owned_row(db, run_id, owner_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM react_events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after),
            )]


class RunDelivery:
    """Commit one validated public answer before the transport can publish it."""

    def __init__(self, storage, coordinator):
        self.storage, self.coordinator = storage, coordinator

    def commit(self, run_id, owner_id, response, *, execution=None, admission=None):
        with self.storage.connection() as db:
            begin_write(db)
            row = RunRegistry.owned_row(db, run_id, owner_id)
            if row["response_json"]:
                return json.loads(row["response_json"])
            self.coordinator.assert_current(db, run_id, execution, admission)
            if not row["active"]:
                raise RunConflict("Run is already terminal")
            if not row["cancelled"] and row["run_status"] in {"pausing", "paused"}:
                raise RunPauseRequested(row)
            response = {**response, "run_id": run_id, "session_id": row["session_id"]}
            if row["cancelled"]:
                response.update(task_status="cancelled", stop_reason="cancelled",
                                answer="任务已取消，未继续生成研究结论。")
            dependencies = response.get("memory_dependencies") or []
            has_dependencies = "memory_dependencies" in response
            if not row["cancelled"] and has_dependencies:
                from app.repositories.long_term_memory_repository import (
                    context_identity_in_connection,
                    dependencies_unchanged_in_connection,
                )

                if dependencies and not dependencies_unchanged_in_connection(
                    db, owner_id, dependencies,
                ):
                    identity = context_identity_in_connection(db, owner_id)
                    # A used preference may have been deleted or updated while
                    # the model was generating. Unrelated memories do not block
                    # delivery because they were not dependencies of this answer.
                    response.update(task_status="partial", stop_reason="memory_changed",
                                    answer="长期记忆在本次生成期间发生变化，旧候选答案未发布。请重新发起查询。",
                                    memory_owner_revision=identity["owner_revision"],
                                    memory_context_fingerprint=identity["context_fingerprint"],
                                    memory_context_used=False, stock_mentions=[], suggested_questions=[])
            elif not row["cancelled"] and (response.get("memory_owner_revision") is not None
                                          or response.get("memory_context_fingerprint") is not None):
                # Compatibility for responses queued before dependency tracking.
                from app.repositories.long_term_memory_repository import context_identity_in_connection

                identity = context_identity_in_connection(db, owner_id)
                if (response.get("memory_owner_revision") != identity["owner_revision"]
                        or response.get("memory_context_fingerprint") != identity["context_fingerprint"]):
                    response.update(task_status="partial", stop_reason="memory_changed",
                                    answer="长期记忆在本次生成期间发生变化，旧候选答案未发布。请重新发起查询。",
                                    memory_owner_revision=identity["owner_revision"],
                                    memory_context_fingerprint=identity["context_fingerprint"],
                                    memory_context_used=False, stock_mentions=[], suggested_questions=[])
            # Graph final validation owns evidence/compliance checks. This boundary
            # validates and normalizes the exact public document before committing.
            response = AgentChatResponse.model_validate(response).model_dump(mode="json")
            now = datetime.now(timezone.utc).isoformat()
            status = "error" if response["task_status"] == "error" else "success"
            run_status = ("failed" if response["task_status"] == "error" else
                          "cancelled" if response["task_status"] == "cancelled" else "completed")
            serialized = encode(response)
            if response.get("memory_action"):
                # Both halves of a memory command remain visible in the chat
                # ledger, but neither can later become a session preference.
                user = db.execute("SELECT metadata_json FROM chat_messages WHERE message_id=? AND run_id=?",
                                  ("user_" + run_id, run_id)).fetchone()
                if user:
                    metadata = json.loads(user["metadata_json"] or "{}")
                    metadata.update(memory_scope="long_term", memory_action=response["memory_action"])
                    db.execute("UPDATE chat_messages SET metadata_json=? WHERE message_id=? AND run_id=?",
                               (encode(metadata), "user_" + run_id, run_id))
            db.execute("""INSERT INTO agent_runs
                (run_id,session_id,run_type,status,intent,tool_calls_json,input_json,output_json,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, row["session_id"], "agent_chat", status,
                response["intent"], encode(response.get("tool_calls", [])), row["request_json"],
                serialized, row["started_at"], now))
            db.execute("""INSERT INTO chat_messages
                (message_id,session_id,role,content,status,run_id,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", ("answer_" + run_id, row["session_id"], "assistant",
                response["answer"], status, run_id, serialized, now))
            db.execute(
                "UPDATE chat_sessions SET updated_at=? WHERE session_id=? AND owner_id=?",
                (now, row["session_id"], owner_id),
            )
            db.execute(
                """UPDATE react_runs SET response_json=?,active=0,run_status=?,updated_at=?,finished_at=?
                WHERE run_id=? AND owner_id=?""",
                (serialized, run_status, now, now, run_id, owner_id),
            )
            from app.services.memory_extraction import enqueue_memory_extraction_in_connection

            enqueue_memory_extraction_in_connection(
                db, run_id=run_id, owner_id=owner_id, session_id=row["session_id"],
                source_message_id="user_" + run_id,
                task_status=response.get("task_status"),
                memory_action=response.get("memory_action"),
            )
            return response


class AgentRunServices:
    """One shared storage boundary, with explicit independently testable roles."""

    def __init__(self, database_url=None):
        self.storage = RunStorage(database_url)
        self.coordinator = RunCoordinator(self.storage)
        self.registry = RunRegistry(self.storage, self.coordinator)
        self.delivery = RunDelivery(self.storage, self.coordinator)
        self.events = RunEventStore(self.storage, self.coordinator)
        from app.services.agent_run_control import RunControlService
        self.controls = RunControlService(self.storage, self.coordinator)
