"""Durable run journal with scoped idempotency and fenced execution writes."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.database import begin_write, connect, initialize_database, is_integrity_error, is_postgres
from app.quality_schema import initialize_quality_schema, enqueue_completed
from app.services.runtime_coordination import LeaseLost, LeaseStore

CURRENT_CONTROL = ContextVar("react_run_control", default=None)


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class RunConflict(ValueError):
    pass


class Journal:
    def __init__(self, database_path=None):
        self.database_path = database_path
        self.leases = LeaseStore(database_path)
        with self.connection() as db:
            initialize_database(db)
            if is_postgres(db):
                # PostgreSQL schema changes are applied by Alembic before startup.
                return
            db.executescript("""
                CREATE TABLE IF NOT EXISTS react_runs (
                    run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    message_key TEXT NOT NULL, request_json TEXT NOT NULL, request_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, cancelled INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL, checkpoint_json TEXT, response_json TEXT,
                    UNIQUE(owner_id, session_id, message_key),
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS react_active_session
                    ON react_runs(owner_id, session_id) WHERE active=1;
                CREATE TABLE IF NOT EXISTS react_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    event TEXT NOT NULL, payload_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES react_runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS react_calls (
                    run_id TEXT NOT NULL, call_id TEXT NOT NULL, signature TEXT NOT NULL,
                    result_json TEXT,
                    PRIMARY KEY(run_id,call_id),
                    FOREIGN KEY(run_id) REFERENCES react_runs(run_id) ON DELETE CASCADE
                );
            """)
            initialize_quality_schema(db)

    @contextmanager
    def connection(self):
        db = connect(self.database_path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request, owner_id):
        payload = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
        key = request.message_id or uuid4().hex
        with self.connection() as db:
            begin_write(db)
            existing = db.execute("SELECT * FROM react_runs WHERE owner_id=? AND session_id=? AND message_key=?",
                                  (owner_id, request.session_id, key)).fetchone()
            if existing:
                if existing["request_hash"] != fingerprint:
                    raise RunConflict("同一 message_id 不能用于不同请求")
                return dict(existing), False
            identity = db.execute("SELECT 1 FROM chat_sessions WHERE session_id=? AND owner_id=?", (request.session_id, owner_id)).fetchone()
            if identity is None:
                raise KeyError("Session not found")
            run_id = "run_" + uuid4().hex
            try:
                db.execute("INSERT INTO react_runs(run_id,owner_id,session_id,message_key,request_json,request_hash,started_at) VALUES(?,?,?,?,?,?,?)",
                           (run_id, owner_id, request.session_id, key, encode(payload), fingerprint, datetime.now(timezone.utc).isoformat()))
            except Exception as error:
                if not is_integrity_error(error):
                    raise
                raise RunConflict("该会话已有任务执行中，请等待或取消后再发送") from error
            return dict(db.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()), True

    def get(self, run_id, owner_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM react_runs WHERE run_id=? AND owner_id=?", (run_id, owner_id)).fetchone()
            if row is None:
                raise KeyError("Run not found")
            return dict(row)

    def discard_unstarted(self, run_id, owner_id, execution=None):
        """Release a rejected reservation without deleting recoverable work."""
        with self.connection() as db:
            begin_write(db)
            try:
                self.leases.assert_current(db, "react:" + run_id, execution)
            except LeaseLost:
                return False
            deleted = db.execute("""
                DELETE FROM react_runs
                WHERE run_id=? AND owner_id=? AND active=1 AND cancelled=0
                  AND checkpoint_json IS NULL AND response_json IS NULL
                  AND NOT EXISTS (SELECT 1 FROM react_events WHERE run_id=react_runs.run_id)
                  AND NOT EXISTS (SELECT 1 FROM react_calls WHERE run_id=react_runs.run_id)
                  AND NOT EXISTS (SELECT 1 FROM chat_messages WHERE run_id=react_runs.run_id)
            """, (run_id, owner_id))
            return deleted.rowcount == 1

    def claim(self, run_id):
        return self.leases.claim("react:" + run_id)

    def executing(self, run_id):
        return self.leases.live("react:" + run_id)

    def assert_execution(self, db, run_id):
        control = CURRENT_CONTROL.get()
        execution = (getattr(control, "execution", None)
                     if control is not None and control.row["run_id"] == run_id else None)
        self.leases.assert_current(db, "react:" + run_id, execution)
        admission = getattr(control, "admission", None)
        if admission is not None and hasattr(admission, "assert_current"):
            admission.assert_current(db)

    def begin_execution_write(self, db, run_id):
        begin_write(db)
        self.assert_execution(db, run_id)

    def event(self, run_id, event, payload):
        with self.connection() as db:
            self.begin_execution_write(db, run_id)
            db.execute("INSERT INTO react_events(run_id,event,payload_json) VALUES(?,?,?)", (run_id, event, encode(payload)))

    def events(self, run_id, owner_id, after=0):
        self.get(run_id, owner_id)
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM react_events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after))]

    def checkpoint(self, run_id, owner_id, payload):
        with self.connection() as db:
            self.begin_execution_write(db, run_id)
            db.execute("UPDATE react_runs SET checkpoint_json=? WHERE run_id=? AND owner_id=? AND active=1", (encode(payload), run_id, owner_id))

    def finish(self, run_id, owner_id, response):
        """Publish one immutable answer, chat message and run in one transaction."""
        with self.connection() as db:
            begin_write(db)
            row = db.execute("SELECT * FROM react_runs WHERE run_id=? AND owner_id=?", (run_id, owner_id)).fetchone()
            if row is None:
                raise KeyError("Run not found")
            if row["response_json"]:
                return json.loads(row["response_json"])
            self.assert_execution(db, run_id)
            response = {**response, "run_id": run_id, "session_id": row["session_id"]}
            if row["cancelled"]:
                response.update(task_status="cancelled", stop_reason="cancelled", answer="任务已取消，未继续生成研究结论。")
            now = datetime.now(timezone.utc).isoformat()
            status = "error" if response["task_status"] == "error" else "success"
            serialized = encode(response)
            db.execute("""INSERT INTO agent_runs
                (run_id,session_id,run_type,status,intent,tool_calls_json,input_json,output_json,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, row["session_id"], "agent_chat", status,
                response["intent"], encode(response.get("tool_calls", [])), row["request_json"], serialized, row["started_at"], now))
            db.execute("""INSERT INTO chat_messages
                (message_id,session_id,role,content,status,run_id,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", ("answer_" + run_id, row["session_id"], "assistant",
                response["answer"], status, run_id, serialized, now))
            db.execute("UPDATE chat_sessions SET updated_at=? WHERE session_id=? AND owner_id=?", (now, row["session_id"], owner_id))
            db.execute("UPDATE react_runs SET response_json=?,active=0 WHERE run_id=? AND owner_id=?", (serialized, run_id, owner_id))
            enqueue_completed(db, row, response)
            return response

    def cancel(self, run_id, owner_id):
        self.get(run_id, owner_id)
        with self.connection() as db:
            begin_write(db)
            db.execute("UPDATE react_runs SET cancelled=1 WHERE run_id=? AND owner_id=? AND active=1", (run_id, owner_id))

    def begin_call(self, run_id, call):
        signature = encode([call["name"], sorted(call["args"].items())])
        with self.connection() as db:
            begin_write(db)
            old = db.execute("SELECT * FROM react_calls WHERE run_id=? AND call_id=?", (run_id, call["id"])).fetchone()
            if old:
                if old["signature"] != signature:
                    raise RunConflict("Call id reused with different arguments")
                return json.loads(old["result_json"]) if old["result_json"] else {
                    "error": "Previous execution outcome is unknown; not automatically repeated",
                    "error_type": "UncertainExecution", "result_state": "error", "execution_status": "failed",
                }
            self.assert_execution(db, run_id)
            db.execute("INSERT INTO react_calls(run_id,call_id,signature) VALUES(?,?,?)", (run_id, call["id"], signature))
        return None

    def finish_call(self, run_id, call_id, result):
        with self.connection() as db:
            self.begin_execution_write(db, run_id)
            db.execute("UPDATE react_calls SET result_json=? WHERE run_id=? AND call_id=?", (encode(result), run_id, call_id))

class RunControl:
    def __init__(self, journal, row, execution=None, admission=None):
        self.journal, self.row = journal, row
        self.execution = execution
        self.admission = admission

    def cancelled(self):
        try:
            if self.execution is not None:
                with self.journal.connection() as db:
                    self.journal.leases.assert_current(db, "react:" + self.row["run_id"], self.execution)
                    if self.admission is not None and hasattr(self.admission, "assert_current"):
                        self.admission.assert_current(db)
            return bool(self.journal.get(self.row["run_id"], self.row["owner_id"])["cancelled"])
        except (KeyError, LeaseLost):
            return True

    def save(self, payload):
        self.journal.checkpoint(self.row["run_id"], self.row["owner_id"], payload)

    def begin_call(self, call):
        from app.services.quality_trace import record_snapshot
        previous = self.journal.begin_call(self.row["run_id"], call)
        record_snapshot(self, "tool_start", {
            "call_id": call["id"], "name": call["name"], "arguments": call["args"],
            "state": "replayed" if previous is not None else "reserved",
        })
        return previous

    def finish_call(self, call_id, result):
        self.journal.finish_call(self.row["run_id"], call_id, result)
        from app.services.quality_trace import record_snapshot
        record_snapshot(self, "tool_end", {"call_id": call_id, "result": result})
