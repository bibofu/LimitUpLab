"""Atomic model-call reservations and audit for manually requested Judge jobs."""

from datetime import datetime, timedelta, timezone
import math
from uuid import uuid4

from app.database import begin_write
from app.quality_models import QualityConflict
from app.quality_schema import now
from app.repositories.storage_sql import database_clock
from app.services.quality_trace import scrub_private_data


class JudgeBudgetExceeded(QualityConflict):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _expire_calls(db):
    # An expired request has an unknown outcome. Its quota remains consumed; a
    # later provider response can enrich the call audit without changing reports.
    db.execute("""UPDATE quality_judge_calls SET status='unknown',
        error_type='CallDeadlineExceeded',finished_at=?
        WHERE status IN ('reserved','started') AND expires_at<=?""", (now(), database_clock(db)))


def judge_capacity_available(db, limit):
    _integer(limit, "max_concurrency", 1)
    _expire_calls(db)
    count = db.execute("""SELECT count(*) FROM (
        SELECT id FROM quality_jobs WHERE job_kind='llm_judge' AND status='running' AND lease_until>?
        UNION SELECT job_id FROM quality_judge_calls WHERE status IN ('reserved','started')) AS active_jobs""",
        (database_clock(db),)).fetchone()[0]
    return count < limit


class JudgeRepositoryMixin:
    def reserve_judge_call(self, job, call_key, *, daily_limit, job_limit,
                           max_concurrency=1, timeout_seconds=120, phase="assessment", request_hash=None):
        for name, value, minimum in (("daily_limit", daily_limit, 0), ("job_limit", job_limit, 1),
                                      ("max_concurrency", max_concurrency, 1)):
            _integer(value, name, minimum)
        if not isinstance(call_key, str) or not 0 < len(call_key) <= 100:
            raise ValueError("call_key must be a nonempty bounded string")
        if not isinstance(phase, str) or not 0 < len(phase) <= 40:
            raise ValueError("phase must be a nonempty bounded string")
        if request_hash is not None and (not isinstance(request_hash, str) or len(request_hash) != 64
                or any(character not in "0123456789abcdef" for character in request_hash)):
            raise ValueError("request_hash must be a SHA-256 hexadecimal digest")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be finite and between 1 and 600")
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            if row["job_kind"] != "llm_judge":
                raise ValueError("Only Judge jobs can reserve a model call")
            _expire_calls(db)
            if db.execute("SELECT 1 FROM quality_judge_calls WHERE job_id=? AND call_key=?", (row["id"], call_key)).fetchone():
                raise QualityConflict("A model-call reservation cannot be reused")
            used = db.execute("SELECT count(*) FROM quality_judge_calls WHERE job_id=?", (row["id"],)).fetchone()[0]
            if used >= job_limit:
                raise JudgeBudgetExceeded("job_limit")
            active = db.execute("SELECT count(*) FROM quality_judge_calls WHERE status IN ('reserved','started')").fetchone()[0]
            if active >= max_concurrency:
                raise JudgeBudgetExceeded("concurrency_limit")
            clock = database_clock(db)
            period = datetime.fromtimestamp(clock, timezone.utc).date().isoformat()
            db.execute("INSERT INTO quality_budget_usage(job_kind,period,metric) VALUES('llm_judge',?,'model_calls') ON CONFLICT DO NOTHING", (period,))
            updated = db.execute("""UPDATE quality_budget_usage SET reserved_units=reserved_units+1
                WHERE job_kind='llm_judge' AND period=? AND metric='model_calls' AND reserved_units<?""",
                (period, daily_limit)).rowcount
            if not updated:
                raise JudgeBudgetExceeded("daily_limit")
            evaluation = db.execute("SELECT evaluation_id FROM quality_evaluations WHERE job_id=? AND attempt=? AND execution_status='running'",
                                    (row["id"], row["attempts"])).fetchone()
            if evaluation is None:
                raise QualityConflict("Judge attempt is not running")
            call_id, stamp = "judge_call_" + uuid4().hex, now()
            db.execute("""INSERT INTO quality_judge_calls(call_id,job_id,evaluation_id,attempt,call_key,period,
                phase,request_hash,timeout_seconds,expires_at,reserved_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (call_id, row["id"], evaluation[0], row["attempts"], call_key, period, scrub_private_data(phase),
                 request_hash, timeout_seconds, clock + timeout_seconds + 5, stamp))
            db.execute("""INSERT INTO quality_budget_reservations(job_id,attempt,reservation_key,job_kind,period,metric,units,created_at)
                VALUES(?,?,?,'llm_judge',?,'model_calls',1,?)""", (row["id"], row["attempts"], call_id, period, stamp))
            return dict(db.execute("SELECT * FROM quality_judge_calls WHERE call_id=?", (call_id,)).fetchone())

    def start_judge_call(self, job, call_id):
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            _expire_calls(db)
            changed = db.execute("""UPDATE quality_judge_calls SET status='started',started_at=?,
                expires_at=?+timeout_seconds+5 WHERE call_id=? AND job_id=? AND attempt=? AND status='reserved'""",
                (now(), database_clock(db), call_id, row["id"], row["attempts"])).rowcount
            if not changed:
                raise QualityConflict("Judge call cannot be started or was already issued")
            return dict(db.execute("SELECT * FROM quality_judge_calls WHERE call_id=?", (call_id,)).fetchone())

    def finish_judge_call(self, call_id, *, status, usage=None, error_type=None, duration_ms=None):
        if status not in {"succeeded", "error"}:
            raise ValueError("Invalid Judge call outcome")
        usage = usage or {}
        tokens = [usage.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
        for value in tokens:
            if value is not None:
                _integer(value, "token usage")
        if duration_ms is not None and (isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float))
                or not math.isfinite(duration_ms) or duration_ms < 0):
            raise ValueError("duration_ms must be finite and nonnegative")
        error_type = str(scrub_private_data(error_type))[:200] if error_type is not None else None
        with self.connection() as db:
            begin_write(db)
            row = db.execute("SELECT * FROM quality_judge_calls WHERE call_id=?", (call_id,)).fetchone()
            if row is None:
                return {"recorded": False, "reason": "deleted"}
            if row["settled_at"]:
                raise QualityConflict("Judge call outcome was already recorded")
            stamp = now()
            if row["status"] == "unknown":
                db.execute("""UPDATE quality_judge_calls SET late_status=?,late_error_type=?,settled_at=?,
                    prompt_tokens=?,completion_tokens=?,total_tokens=?,duration_ms=? WHERE call_id=?""",
                    (status, error_type, stamp, *tokens, duration_ms, call_id))
            else:
                db.execute("""UPDATE quality_judge_calls SET status=?,error_type=?,finished_at=?,settled_at=?,
                    prompt_tokens=?,completion_tokens=?,total_tokens=?,duration_ms=? WHERE call_id=?""",
                    (status, error_type, stamp, stamp, *tokens, duration_ms, call_id))
            return {"recorded": True, **dict(db.execute("SELECT * FROM quality_judge_calls WHERE call_id=?", (call_id,)).fetchone())}

    def defer_judge_job(self, job, reason, *, metadata=None):
        if reason not in {"daily_limit", "job_limit", "concurrency_limit"}:
            raise ValueError("Unsupported Judge deferral reason")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Judge deferral metadata must be an object")
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            if row["job_kind"] != "llm_judge":
                raise ValueError("Only Judge jobs can be deferred")
            report = {"evaluator_kind": "llm_judge", "evaluator_version": row["detector_version"],
                "execution_status": "skipped", "verdict": "unknown", "checks": [],
                "metadata": {**(metadata or {}), "reason": reason, "model_calls": db.execute(
                    "SELECT count(*) FROM quality_judge_calls WHERE job_id=? AND attempt=?",
                    (row["id"], row["attempts"])).fetchone()[0]}}
            self._end_attempt(db, row, report, reason)
            clock = database_clock(db)
            tomorrow = datetime.fromtimestamp(clock, timezone.utc).date() + timedelta(days=1)
            available_at = datetime.combine(tomorrow, datetime.min.time(), timezone.utc).timestamp() if reason == "daily_limit" else clock + 5
            status = "dead" if reason == "job_limit" else "pending"
            db.execute("""UPDATE quality_jobs SET status=?,available_at=?,max_attempts=max_attempts+?,
                lease_token=NULL,lease_until=NULL,last_error=?,updated_at=? WHERE id=?""",
                (status, available_at, int(status == "pending"), reason, now(), row["id"]))
            return {"status": status, "reason": reason, "available_at": available_at,
                    "evaluation_id": job["evaluation_id"]}

    def judge_call_history(self, job_id):
        with self.connection() as db:
            begin_write(db)
            _expire_calls(db)
            return {"items": [dict(row) for row in db.execute(
                "SELECT * FROM quality_judge_calls WHERE job_id=? ORDER BY reserved_at,call_id", (job_id,))]}

    def job_history(self, run_id, *, limit=50, offset=0):
        with self.connection() as db:
            begin_write(db)
            _expire_calls(db)
            if not db.execute("SELECT 1 FROM react_runs WHERE run_id=?", (run_id,)).fetchone():
                raise KeyError("Run not found")
            total = db.execute("SELECT count(*) FROM quality_jobs WHERE run_id=?", (run_id,)).fetchone()[0]
            jobs = db.execute("""SELECT id AS job_id,run_id,bundle_id,job_kind,detector_version AS evaluator_version,
                status,attempts,max_attempts,available_at,last_error,created_at,updated_at FROM quality_jobs
                WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (run_id, max(1, min(limit, 100)), max(0, offset))).fetchall()
            items = []
            for job in jobs:
                item = dict(job)
                item["id"] = item["job_id"]
                item["calls"] = [dict(row) for row in db.execute(
                    "SELECT * FROM quality_judge_calls WHERE job_id=? ORDER BY reserved_at,call_id", (job["job_id"],))]
                evaluation = db.execute("SELECT evaluation_id FROM quality_evaluations WHERE job_id=? ORDER BY attempt DESC LIMIT 1", (job["job_id"],)).fetchone()
                item["evaluation_id"] = evaluation[0] if evaluation else None
                items.append(item)
            return scrub_private_data({"items": items, "total": total})
