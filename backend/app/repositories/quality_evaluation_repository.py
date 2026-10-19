"""Immutable evaluation attempts over frozen evidence, with leased work and quotas."""

import json
from uuid import uuid4

from app.database import begin_write
from app.quality_models import DetectionSignal, QualityConflict
from app.quality_schema import DETECTOR_VERSION, answer_hash, canonical_hash, default_config, encode, enqueue_completed, now
from app.repositories.storage_sql import database_clock
from app.services.quality_trace import scrub_private_data


def _unpack(row):
    result = dict(row)
    raw = result.pop("report_json", None)
    result["report"] = json.loads(raw) if raw else None
    return result


class EvaluationRepositoryMixin:
    def _existing_bundle(self, db, run_id):
        row = db.execute("""SELECT bundle_id FROM quality_evidence_bundles WHERE run_id=?
            ORDER BY (capture_mode='completion') DESC,created_at DESC,bundle_id LIMIT 1""", (run_id,)).fetchone()
        return row[0] if row else None

    def enqueue_evaluation(self, run_id, *, job_kind="deterministic", evaluator_version=DETECTOR_VERSION, config=None, bundle_id=None):
        with self.connection() as db:
            begin_write(db)
            row = db.execute("SELECT * FROM react_runs WHERE run_id=? AND response_json IS NOT NULL", (run_id,)).fetchone()
            if row is None:
                raise KeyError("Completed run not found")
            bundle_id = bundle_id or self._existing_bundle(db, run_id)
            effective_config = scrub_private_data(default_config() if config is None else config)
            created = enqueue_completed(db, row, json.loads(row["response_json"]), evaluator_version,
                job_kind=job_kind, config=effective_config, bundle_id=bundle_id, capture_mode="historical")
            bundle_id = bundle_id or self._existing_bundle(db, run_id)
            key = canonical_hash([job_kind, evaluator_version, canonical_hash(effective_config), bundle_id])
            return {"created": bool(created), "job_id": key, "bundle_id": bundle_id}

    def backfill_jobs(self, limit=100):
        with self.connection() as db:
            begin_write(db)
            rows = db.execute("""SELECT r.* FROM react_runs r WHERE r.response_json IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM quality_jobs j WHERE j.run_id=r.run_id
                    AND j.job_kind='deterministic' AND j.detector_version=?)
                ORDER BY r.started_at LIMIT ?""", (DETECTOR_VERSION, max(1, min(limit, 1000)))).fetchall()
            return sum(enqueue_completed(db, row, json.loads(row["response_json"]),
                bundle_id=self._existing_bundle(db, row["run_id"]), capture_mode="historical") for row in rows)

    def _end_attempt(self, db, job, report, error_type=None):
        cleaned = scrub_private_data(report)
        db.execute("""UPDATE quality_evaluations SET execution_status=?,verdict=?,report_json=?,error_type=?,finished_at=?
            WHERE job_id=? AND attempt=? AND execution_status='running'""", (cleaned["execution_status"],
            cleaned["verdict"], encode(cleaned), error_type, now(), job["id"], job["attempts"]))

    def _error_report(self, job, reason):
        return {"evaluator_kind": job["job_kind"], "evaluator_version": job["detector_version"],
                "execution_status": "error", "verdict": "unknown", "checks": [],
                "metadata": {"error_type": reason, "model_calls": 0 if job["job_kind"] == "deterministic" else None}}

    def claim_job(self, worker_id, lease_seconds=60, *, job_kind="deterministic", evaluator_version=None, max_concurrency=1):
        if job_kind not in {"deterministic", "llm_judge"}:
            raise ValueError("Unsupported evaluator kind")
        evaluator_version = evaluator_version or (DETECTOR_VERSION if job_kind == "deterministic" else None)
        if evaluator_version is None:
            raise ValueError("A non-deterministic worker must select its evaluator version")
        with self.connection() as db:
            begin_write(db)
            clock, stamp = database_clock(db), now()
            expired = db.execute("""SELECT * FROM quality_jobs WHERE job_kind=? AND detector_version=?
                AND status='running' AND lease_until<=?""", (job_kind, evaluator_version, clock)).fetchall()
            for job in expired:
                self._end_attempt(db, job, self._error_report(job, "LeaseExpired"), "LeaseExpired")
                db.execute("""UPDATE quality_jobs SET status=?,lease_token=NULL,lease_until=NULL,
                    last_error='LeaseExpired',updated_at=? WHERE id=?""",
                    ("dead" if job["attempts"] >= job["max_attempts"] else "pending", stamp, job["id"]))
            if job_kind == "llm_judge":
                from app.repositories.quality_judge_repository import judge_capacity_available
                if not judge_capacity_available(db, max_concurrency):
                    return None
            job = db.execute("""SELECT * FROM quality_jobs WHERE job_kind=? AND detector_version=?
                AND bundle_id IS NOT NULL AND status='pending' AND attempts<max_attempts AND available_at<=?
                ORDER BY available_at,id LIMIT 1""", (job_kind, evaluator_version, clock)).fetchone()
            if job is None:
                return None
            token = uuid4().hex
            db.execute("""UPDATE quality_jobs SET status='running',attempts=attempts+1,lease_token=?,
                lease_until=?,worker_id=?,updated_at=? WHERE id=?""",
                (token, clock + max(1, min(lease_seconds, 600)), worker_id, stamp, job["id"]))
            claimed = dict(db.execute("SELECT * FROM quality_jobs WHERE id=?", (job["id"],)).fetchone())
            evaluation_id = "evaluation_" + uuid4().hex
            db.execute("""INSERT INTO quality_evaluations(evaluation_id,job_id,run_id,bundle_id,attempt,
                evaluator_kind,evaluator_version,config_hash,answer_hash,execution_status,started_at)
                VALUES(?,?,?,?,?,?,?,?,?,'running',?)""", (evaluation_id, claimed["id"], claimed["run_id"],
                claimed["bundle_id"], claimed["attempts"], job_kind, evaluator_version,
                claimed["config_hash"], claimed["answer_hash"], stamp))
            bundle = json.loads(db.execute("SELECT payload_json FROM quality_evidence_bundles WHERE bundle_id=?", (claimed["bundle_id"],)).fetchone()[0])
            observed = {"captured_at": stamp, "delivery_events": [dict(r) for r in db.execute(
                "SELECT * FROM quality_delivery WHERE run_id=? ORDER BY created_at", (claimed["run_id"],))]}
            claimed["config"] = json.loads(claimed.pop("config_json"))
            return {**{key: bundle.get(key) for key in ("request", "response", "checkpoint", "calls")},
                    **claimed, "evaluation_id": evaluation_id, "bundle": bundle,
                    "trace_snapshots": bundle.get("diagnostic_snapshots", []),
                    "transport_observation": observed, "delivery_events": observed["delivery_events"]}

    def _owned_job(self, db, job):
        row = db.execute("SELECT * FROM quality_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                         (job["id"], job["lease_token"], database_clock(db))).fetchone()
        if row is None:
            raise QualityConflict("Quality job lease has expired or changed")
        return row

    def renew_job(self, job, lease_seconds=60):
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            until = database_clock(db) + max(1, min(lease_seconds, 600))
            db.execute("UPDATE quality_jobs SET lease_until=?,updated_at=? WHERE id=?", (until, now(), row["id"]))
            return {"lease_until": until}

    def complete_job(self, job, signals, report=None):
        from app.quality_evaluation_models import EvaluationReport
        signals = [DetectionSignal.model_validate(s).model_dump() for s in signals]
        report = report or {"evaluator_kind": job["job_kind"], "evaluator_version": job["detector_version"],
            "execution_status": "succeeded", "verdict": "unknown", "checks": [],
            "metadata": {"legacy_signal_only": True, "model_calls": 0}}
        report = EvaluationReport.model_validate(report).model_dump(mode="json")
        if report["execution_status"] != "succeeded" and signals:
            raise ValueError("Unsuccessful assessments cannot publish signals")
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            if report["evaluator_kind"] != row["job_kind"] or report["evaluator_version"] != row["detector_version"]:
                raise ValueError("Report evaluator differs from leased job")
            if report["execution_status"] == "error":
                raise ValueError("Failed evaluations must use fail_job")
            if row["job_kind"] == "deterministic" and report.get("metadata", {}).get("model_calls", 0) != 0:
                raise ValueError("Deterministic evaluation cannot invoke models")
            run = db.execute("SELECT response_json FROM react_runs WHERE run_id=?", (row["run_id"],)).fetchone()
            response = json.loads(run["response_json"])
            if answer_hash(response) != row["answer_hash"]:
                raise QualityConflict("Completed answer identity changed")
            for signal in signals:
                if signal["detector_version"] != row["detector_version"]:
                    raise ValueError("Signal detector version differs from job")
                self._signal(db, row["run_id"], response, signal,
                             evaluation_key="" if signal["source"] in {"delivery", "feedback"} else row["id"])
            self._end_attempt(db, row, report)
            db.execute("UPDATE quality_jobs SET status='done',lease_token=NULL,lease_until=NULL,last_error=NULL,updated_at=? WHERE id=?", (now(), row["id"]))
            evaluation = db.execute("SELECT evaluation_id FROM quality_evaluations WHERE job_id=? AND attempt=?", (row["id"], row["attempts"])).fetchone()[0]
        return {"status": "done", "signal_count": len(signals), "evaluation_id": evaluation}

    def fail_job(self, job, error, report=None):
        from app.quality_evaluation_models import EvaluationReport
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            error_type = scrub_private_data(error if isinstance(error, str) else type(error).__name__)[:1000]
            report = EvaluationReport.model_validate(report or self._error_report(row, error_type)).model_dump(mode="json")
            if (report["execution_status"] != "error" or report["evaluator_kind"] != row["job_kind"]
                    or report["evaluator_version"] != row["detector_version"]):
                raise ValueError("Failure report must describe this evaluator's execution error")
            self._end_attempt(db, row, report, error_type)
            status = "dead" if row["attempts"] >= row["max_attempts"] else "pending"
            db.execute("""UPDATE quality_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,
                last_error=?,updated_at=? WHERE id=?""", (status, database_clock(db) + min(60, 2 ** row["attempts"]), error_type, now(), row["id"]))
            evaluation = db.execute("SELECT evaluation_id FROM quality_evaluations WHERE job_id=? AND attempt=?", (row["id"], row["attempts"])).fetchone()[0]
            return {"status": status, "attempts": row["attempts"], "evaluation_id": evaluation}

    def get_bundle(self, bundle_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM quality_evidence_bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
            if row is None:
                raise KeyError("Evidence bundle not found")
            return {**json.loads(row["payload_json"]), **{key: row[key] for key in ("bundle_id", "content_hash", "answer_hash")}}

    def evaluation_history(self, run_id, *, limit=50, offset=0):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM react_runs WHERE run_id=?", (run_id,)).fetchone():
                raise KeyError("Run not found")
            total = db.execute("SELECT count(*) FROM quality_evaluations WHERE run_id=?", (run_id,)).fetchone()[0]
            rows = db.execute("""SELECT * FROM quality_evaluations WHERE run_id=?
                ORDER BY started_at DESC,evaluation_id DESC LIMIT ? OFFSET ?""", (run_id, max(1, min(limit, 100)), max(0, offset)))
            return scrub_private_data({"items": [_unpack(row) for row in rows], "total": total})

    def list_evaluations(self, *, verdict=None, execution_status=None, evaluator_kind=None, limit=20, offset=0):
        clauses, params = [], []
        for column, value in (("verdict", verdict), ("execution_status", execution_status), ("evaluator_kind", evaluator_kind)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            total = db.execute("SELECT count(*) FROM quality_evaluations" + where, params).fetchone()[0]
            rows = db.execute("SELECT * FROM quality_evaluations" + where + " ORDER BY started_at DESC,evaluation_id DESC LIMIT ? OFFSET ?",
                              (*params, max(1, min(limit, 100)), max(0, offset)))
            return scrub_private_data({"items": [_unpack(row) for row in rows], "total": total})

    def reserve_budget(self, job, reservation_key, *, units, limit, period, metric="model_calls"):
        if type(units) is not int or type(limit) is not int or units < 0 or limit < 0:
            raise ValueError("Budget units and limit must be nonnegative integers")
        if not all(isinstance(item, str) and 0 < len(item) <= 100 for item in (reservation_key, period, metric)):
            raise ValueError("Budget identifiers must be nonempty bounded strings")
        with self.connection() as db:
            begin_write(db)
            row = self._owned_job(db, job)
            if row["job_kind"] == "deterministic" and units:
                raise ValueError("Deterministic evaluation reserves zero model budget")
            old = db.execute("SELECT * FROM quality_budget_reservations WHERE job_id=? AND attempt=? AND reservation_key=?",
                             (row["id"], row["attempts"], reservation_key)).fetchone()
            if old:
                if (old["units"], old["period"], old["metric"]) != (units, period, metric):
                    raise QualityConflict("Budget reservation identity reused with different parameters")
                return dict(old)
            db.execute("INSERT INTO quality_budget_usage(job_kind,period,metric) VALUES(?,?,?) ON CONFLICT DO NOTHING", (row["job_kind"], period, metric))
            updated = db.execute("""UPDATE quality_budget_usage SET reserved_units=reserved_units+?
                WHERE job_kind=? AND period=? AND metric=? AND reserved_units+?<=?""", (units, row["job_kind"], period, metric, units, limit)).rowcount
            if not updated:
                raise QualityConflict("Evaluation budget exhausted")
            db.execute("""INSERT INTO quality_budget_reservations(job_id,attempt,reservation_key,job_kind,period,metric,units,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (row["id"], row["attempts"], reservation_key, row["job_kind"], period, metric, units, now()))
            return {"job_id": row["id"], "attempt": row["attempts"], "reservation_key": reservation_key,
                    "job_kind": row["job_kind"], "period": period, "metric": metric, "units": units}

    def job_health(self):
        with self.connection() as db:
            counts = {state: 0 for state in ("pending", "running", "done", "dead", "superseded")}
            counts.update({r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM quality_jobs GROUP BY status")})
            return {"counts": counts, "by_kind": [dict(r) for r in db.execute("SELECT job_kind,status,count(*) AS count FROM quality_jobs GROUP BY job_kind,status")],
                "failed": [dict(r) for r in db.execute("""SELECT id,run_id,job_kind,attempts,last_error,updated_at
                    FROM quality_jobs WHERE status='dead' ORDER BY updated_at DESC LIMIT 20""")]}
