"""Compatibility boundary for existing run callers during runtime migration.

Business registration, delivery, streaming and coordination live in dedicated
services. The legacy checkpoint/call methods remain for migration compatibility;
the production runner does not use them for execution recovery.
"""

from contextvars import ContextVar
import json

from app.database import begin_write
from app.services.agent_run_services import AgentRunServices, RunConflict, encode
from app.services.runtime_coordination import LeaseLost

CURRENT_CONTROL = ContextVar("react_run_control", default=None)


class Journal:
    """Temporary facade; new integrations should use the named services."""

    def __init__(self, database_url: str | None = None):
        self.services = AgentRunServices(database_url)
        self.leases = self.services.storage.leases
        self.database_url = self.services.storage.database_url

    def connection(self):
        return self.services.storage.connection()

    def create(self, request, owner_id, *, runtime_version="legacy"):
        return self.services.registry.create(request, owner_id, runtime_version=runtime_version)

    def get(self, run_id, owner_id):
        return self.services.registry.get(run_id, owner_id)

    def discard_unstarted(self, run_id, owner_id, execution=None):
        return self.services.registry.discard_unstarted(run_id, owner_id, execution)

    def claim(self, run_id):
        return self.services.coordinator.claim(run_id)

    def executing(self, run_id):
        return self.services.coordinator.executing(run_id)

    @staticmethod
    def execution_context(run_id):
        control = CURRENT_CONTROL.get()
        if control is None or control.row["run_id"] != run_id:
            return {}
        return {"execution": getattr(control, "execution", None),
                "admission": getattr(control, "admission", None)}

    def assert_execution(self, db, run_id):
        self.services.coordinator.assert_current(db, run_id, **self.execution_context(run_id))

    def begin_execution_write(self, db, run_id):
        begin_write(db)
        self.assert_execution(db, run_id)

    def event(self, run_id, event, payload):
        return self.services.events.append(run_id, event, payload, **self.execution_context(run_id))

    def events(self, run_id, owner_id, after=0):
        return self.services.events.list(run_id, owner_id, after)

    def finish(self, run_id, owner_id, response):
        return self.services.delivery.commit(run_id, owner_id, response, **self.execution_context(run_id))

    def cancel(self, run_id, owner_id):
        return self.services.registry.cancel(run_id, owner_id)

    def checkpoint(self, run_id, owner_id, payload):
        with self.connection() as db:
            self.begin_execution_write(db, run_id)
            db.execute(
                "UPDATE react_runs SET checkpoint_json=? WHERE run_id=? AND owner_id=? AND active=1",
                (encode(payload), run_id, owner_id),
            )

    def begin_call(self, run_id, call):
        signature = encode([call["name"], sorted(call["args"].items())])
        with self.connection() as db:
            begin_write(db)
            old = db.execute(
                "SELECT * FROM react_calls WHERE run_id=? AND call_id=?", (run_id, call["id"]),
            ).fetchone()
            if old:
                if old["signature"] != signature:
                    raise RunConflict("Call id reused with different arguments")
                return json.loads(old["result_json"]) if old["result_json"] else {
                    "error": "Previous execution outcome is unknown; not automatically repeated",
                    "error_type": "UncertainExecution", "result_state": "error", "execution_status": "failed",
                }
            self.assert_execution(db, run_id)
            db.execute(
                "INSERT INTO react_calls(run_id,call_id,signature) VALUES(?,?,?)",
                (run_id, call["id"], signature),
            )
        return None

    def finish_call(self, run_id, call_id, result):
        with self.connection() as db:
            self.begin_execution_write(db, run_id)
            db.execute(
                "UPDATE react_calls SET result_json=? WHERE run_id=? AND call_id=?",
                (encode(result), run_id, call_id),
            )


class RunControl:
    def __init__(self, journal, row, execution=None, admission=None):
        self.journal, self.row = journal, row
        self.execution = execution
        self.admission = admission

    def cancelled(self):
        try:
            if self.execution is not None:
                with self.journal.connection() as db:
                    self.journal.services.coordinator.assert_current(
                        db, self.row["run_id"], self.execution, self.admission,
                    )
            return bool(self.journal.get(self.row["run_id"], self.row["owner_id"])["cancelled"])
        except (KeyError, LeaseLost):
            return True

    def save(self, payload):
        self.journal.checkpoint(self.row["run_id"], self.row["owner_id"], payload)

    def begin_call(self, call):
        return self.journal.begin_call(self.row["run_id"], call)

    def finish_call(self, call_id, result):
        self.journal.finish_call(self.row["run_id"], call_id, result)
