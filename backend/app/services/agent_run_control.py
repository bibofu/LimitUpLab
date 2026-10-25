"""Owner-authorized pause/resume intents, independent of graph execution state."""

from datetime import datetime, timezone
import hashlib

from app.database import begin_write
from app.services.agent_run_services import RunConflict, RunRegistry, encode
from app.services.runtime_coordination import LeaseLost


def _now():
    return datetime.now(timezone.utc).isoformat()


def _event(db, row):
    db.execute("INSERT INTO react_events(run_id,event,payload_json) VALUES(?,'run_state',?)",
               (row["run_id"], encode({"run_id": row["run_id"], "run_status": row["run_status"],
                                     "state_revision": row["control_revision"]})))


class RunControlService:
    def __init__(self, storage, coordinator):
        self.storage, self.coordinator = storage, coordinator

    def read(self, run_id, owner_id):
        with self.storage.connection() as db:
            return RunRegistry.owned_row(db, run_id, owner_id)

    def request(self, run_id, owner_id, action, operation_id, expected_state_revision):
        """Replay an operation's receipt without reapplying its old intent.

        The returned row is always current: retrying an old pause operation after
        a later resume must neither pause again nor tell the UI it is paused.
        """
        if action not in {"pause", "resume"}:
            raise ValueError("Unknown run control action")
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
            raise ValueError("operation_id must contain 1 to 128 characters")
        if type(expected_state_revision) is not int or expected_state_revision < 0:
            raise ValueError("expected_state_revision must be a nonnegative integer")
        signature = hashlib.sha256(encode([run_id, action, expected_state_revision]).encode()).hexdigest()
        with self.storage.connection() as db:
            begin_write(db)
            row = RunRegistry.owned_row(db, run_id, owner_id)
            old_revision = row["control_revision"]
            prior = db.execute("SELECT request_hash FROM run_control_operations WHERE owner_id=? AND operation_id=?",
                               (owner_id, operation_id)).fetchone()
            if prior:
                if prior["request_hash"] != signature:
                    raise RunConflict("同一 operation_id 不能用于不同控制请求")
                return row
            if row["runtime_version"] != "durable-v1":
                raise RunConflict("旧版任务不支持暂停或继续")
            terminal = not row["active"] or bool(row["response_json"])
            if action == "resume" and (terminal or row["cancelled"]):
                raise RunConflict("已结束或取消的任务不能继续")
            if not terminal:
                if row["cancelled"]:
                    raise RunConflict("任务正在取消，不能暂停或继续")
                if row["control_revision"] != expected_state_revision:
                    raise RunConflict("任务控制状态已变化，请刷新后重试")
                if action == "pause" and row["run_status"] not in {"pausing", "paused"}:
                    db.execute("""UPDATE react_runs SET run_status='pausing',control_revision=control_revision+1,
                        pause_checkpoint_id=NULL,pause_interrupt_id=NULL,pause_mode=NULL,paused_at=NULL,updated_at=?
                        WHERE run_id=? AND owner_id=?""", (_now(), run_id, owner_id))
                elif action == "resume":
                    if row["run_status"] != "paused":
                        raise RunConflict("任务尚未确认暂停，不能继续")
                    db.execute("""UPDATE react_runs SET run_status='resuming',control_revision=control_revision+1,
                        resume_count=resume_count+1,updated_at=? WHERE run_id=? AND owner_id=?""",
                        (_now(), run_id, owner_id))
            row = RunRegistry.owned_row(db, run_id, owner_id)
            if row["control_revision"] != old_revision:
                _event(db, row)
            db.execute("""INSERT INTO run_control_operations
                (owner_id,operation_id,run_id,action,expected_revision,request_hash,result_revision)
                VALUES(?,?,?,?,?,?,?)""", (owner_id, operation_id, run_id, action, expected_state_revision,
                                         signature, row["control_revision"]))
            return row

    def request_pause(self, run_id, owner_id, *, operation_id, expected_revision):
        return self.request(run_id, owner_id, "pause", operation_id, expected_revision)

    def request_resume(self, run_id, owner_id, *, operation_id, expected_revision):
        return self.request(run_id, owner_id, "resume", operation_id, expected_revision)

    def ack_paused(self, run_id, owner_id, *, expected_revision, checkpoint_id, pause_mode="checkpoint",
                   interrupt_id=None, execution, admission=None):
        """A worker confirms an already persisted boundary; it releases leases later."""
        if pause_mode not in {"interrupt", "checkpoint", "delivery"}:
            raise ValueError("Unknown pause mode")
        if checkpoint_id is None and pause_mode != "checkpoint":
            raise ValueError("This pause mode requires a committed checkpoint")
        if execution is None:
            raise LeaseLost("Pause acknowledgement requires an execution lease")
        with self.storage.connection() as db:
            begin_write(db)
            row = RunRegistry.owned_row(db, run_id, owner_id)
            self.coordinator.assert_current(db, run_id, execution, admission)
            if not row["active"] or row["cancelled"] or row["response_json"]:
                raise RunConflict("任务已结束或正在取消，不能确认暂停")
            if row["control_revision"] != expected_revision or row["run_status"] not in {"pausing", "paused"}:
                raise RunConflict("暂停请求已变化，旧确认无效")
            if row["run_status"] == "paused":
                if (row["pause_checkpoint_id"], row["pause_mode"], row["pause_interrupt_id"]) != (
                        checkpoint_id, pause_mode, interrupt_id):
                    raise RunConflict("已确认的暂停位置不能被覆盖")
                return row
            db.execute("""UPDATE react_runs SET run_status='paused',pause_checkpoint_id=?,pause_mode=?,
                pause_interrupt_id=?,paused_at=?,updated_at=? WHERE run_id=? AND owner_id=?""",
                (checkpoint_id, pause_mode, interrupt_id, _now(), _now(), run_id, owner_id))
            row = RunRegistry.owned_row(db, run_id, owner_id)
            _event(db, row)
            return row

    confirm_paused = ack_paused

    def defer_resume(self, run_id, owner_id, *, expected_revision, execution):
        """Failed admission/startup restores paused state without automatic retries."""
        if execution is None:
            raise LeaseLost("Deferring a resume requires an execution lease")
        with self.storage.connection() as db:
            begin_write(db)
            row = RunRegistry.owned_row(db, run_id, owner_id)
            self.coordinator.assert_current(db, run_id, execution)
            if (row["active"] and not row["cancelled"] and not row["response_json"]
                    and row["run_status"] == "resuming" and row["control_revision"] == expected_revision):
                db.execute("""UPDATE react_runs SET run_status='paused',control_revision=control_revision+1,
                    updated_at=? WHERE run_id=? AND owner_id=?""", (_now(), run_id, owner_id))
                _event(db, RunRegistry.owned_row(db, run_id, owner_id))
            return RunRegistry.owned_row(db, run_id, owner_id)
