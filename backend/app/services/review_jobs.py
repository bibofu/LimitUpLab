"""Review sampling, detector jobs and the background review worker."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import review_settings
from app.database import begin_write, connect, initialize_database
from app.review_models import ReviewJob
from app.services.review_cases import ReviewCaseService
from app.services.review_checks import CHECK_VERSION, run_deterministic_checks
from app.services.review_judge import (
    JUDGE_VERSION,
    PROMPT_VERSION,
    JudgeCallLedger,
    JudgeUnavailable,
    get_review_judge_provider,
    run_judge,
)
from app.services.review_signals import ReviewSignalService
from app.services.review_snapshot import load_run_snapshot
from app.services.review_time import shanghai_day_start, shanghai_next_day_start


JOB_LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30
SAMPLE_DETECTOR = "random_sample"
SAMPLE_DETECTOR_VERSION = "sample-v1"
SAMPLE_CONFIG_FINGERPRINT = "sample-lane-v1"
SAMPLE_PRIORITY = 200
DETERMINISTIC_CONFIG_FINGERPRINT = "deterministic-v1"
DETERMINISTIC_PRIORITY = 100
RECHECK_PRIORITY = 90
JUDGE_CONFIG_FINGERPRINT = f"judge:{JUDGE_VERSION}:{PROMPT_VERSION}"
JUDGE_PRIORITY = 50
JUDGE_SAMPLE_PRIORITY = 150


def sample_bucket(run_id: str, salt: str = "review-sample-v1") -> float:
    """Map a run id to a stable value in [0, 1) for reproducible sampling."""

    digest = hashlib.sha256(f"{salt}:{run_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class ReviewJobService:
    """Enqueue sampled runs, claim jobs and run one bounded worker pass."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url
        self.cases = ReviewCaseService(database_url)
        self.signals = ReviewSignalService(database_url)
        self.judge_ledger = JudgeCallLedger(database_url)
        self.worker_id = "review-worker-" + uuid4().hex[:12]

    def sample_recent_runs(self, *, limit: int = 200) -> int:
        """Enqueue the configured share of recent completed answers."""

        settings = review_settings()
        if not settings.enabled or settings.sample_rate <= 0 or settings.sample_daily_limit <= 0:
            return 0
        day_start = shanghai_day_start()
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            used = db.execute(
                """
                SELECT COUNT(*) AS used
                FROM answer_review_jobs
                WHERE kind='sample' AND created_at >= ?
                """,
                (day_start.isoformat(),),
            ).fetchone()
            remaining = settings.sample_daily_limit - int(used["used"] or 0)
            if remaining <= 0:
                return 0
            candidates = db.execute(
                """
                SELECT run.run_id
                FROM react_runs AS run
                WHERE run.response_json IS NOT NULL
                  AND run.started_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_cases AS case_row
                      WHERE case_row.run_id = run.run_id)
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id AND job.kind='sample')
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
                 max(1, min(limit, 500))),
            ).fetchall()
            inserted = 0
            for candidate in candidates:
                if inserted >= remaining:
                    break
                if sample_bucket(candidate["run_id"]) >= settings.sample_rate:
                    continue
                if self._insert_sample_job(db, candidate["run_id"]):
                    inserted += 1
            db.commit()
            return inserted

    def enqueue_manual_sample(self, limit: int = 5) -> int:
        """Let an administrator pull recent unsampled answers into the queue."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            candidates = db.execute(
                """
                SELECT run.run_id
                FROM react_runs AS run
                WHERE run.response_json IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_cases AS case_row
                      WHERE case_row.run_id = run.run_id)
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id AND job.kind='sample')
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 50)),),
            ).fetchall()
            inserted = 0
            for candidate in candidates:
                if self._insert_sample_job(db, candidate["run_id"]):
                    inserted += 1
            db.commit()
            return inserted

    def enqueue_deterministic_recent(self, limit: int = 100) -> int:
        """Queue contract checks for every completed answer in the last day."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            candidates = db.execute(
                """
                SELECT run.run_id
                FROM react_runs AS run
                WHERE run.response_json IS NOT NULL
                  AND run.started_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id
                        AND job.kind='deterministic'
                        AND job.config_fingerprint=?)
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                (
                    (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
                    DETERMINISTIC_CONFIG_FINGERPRINT,
                    max(1, min(limit, 500)),
                ),
            ).fetchall()
            inserted = 0
            for candidate in candidates:
                if self._insert_job(
                    db, candidate["run_id"], "deterministic", DETERMINISTIC_PRIORITY,
                    CHECK_VERSION, DETERMINISTIC_CONFIG_FINGERPRINT,
                ):
                    inserted += 1
            db.commit()
            return inserted

    def enqueue_judge_candidates(self, limit: int = 50) -> int:
        """Queue judge jobs for downvotes, risky statuses and sampled traffic."""

        settings = review_settings()
        if (
            not settings.enabled
            or not settings.judge_enabled
            or settings.judge_daily_call_limit <= 0
        ):
            return 0
        remaining = settings.judge_daily_call_limit - self.judge_ledger.calls_today()
        if remaining <= 0:
            return 0
        now = datetime.now(timezone.utc)
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            downvotes = db.execute(
                """
                SELECT DISTINCT run.run_id, run.started_at
                FROM react_runs AS run
                JOIN answer_review_signals AS signal
                  ON signal.run_id = run.run_id AND signal.kind='downvote'
                WHERE run.response_json IS NOT NULL
                  AND run.started_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id AND job.kind='judge'
                        AND job.config_fingerprint=?)
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                (
                    (now - timedelta(days=7)).isoformat(),
                    JUDGE_CONFIG_FINGERPRINT,
                    max(1, min(limit, 200)),
                ),
            ).fetchall()
            risky = db.execute(
                """
                SELECT run.run_id
                FROM react_runs AS run
                WHERE run.response_json IS NOT NULL
                  AND run.started_at >= ?
                  AND run.response_json::jsonb->>'task_status' IN ('partial','error')
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id AND job.kind='judge'
                        AND job.config_fingerprint=?)
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                (
                    (now - timedelta(hours=24)).isoformat(),
                    JUDGE_CONFIG_FINGERPRINT,
                    max(1, min(limit, 200)),
                ),
            ).fetchall()
            normal = db.execute(
                """
                SELECT run.run_id
                FROM react_runs AS run
                WHERE run.response_json IS NOT NULL
                  AND run.started_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_jobs AS job
                      WHERE job.run_id = run.run_id AND job.kind='judge'
                        AND job.config_fingerprint=?)
                ORDER BY run.started_at DESC
                LIMIT ?
                """,
                (
                    (now - timedelta(hours=24)).isoformat(),
                    JUDGE_CONFIG_FINGERPRINT,
                    max(1, min(limit, 500)),
                ),
            ).fetchall()
            inserted = 0
            for rows, priority, apply_rate in (
                (downvotes, JUDGE_PRIORITY, False),
                (risky, JUDGE_PRIORITY, False),
                (normal, JUDGE_SAMPLE_PRIORITY, True),
            ):
                for candidate in rows:
                    if inserted >= remaining:
                        break
                    if apply_rate and sample_bucket(
                        candidate["run_id"], salt="review-judge-v1",
                    ) >= settings.judge_sample_rate:
                        continue
                    if self._insert_job(
                        db, candidate["run_id"], "judge", priority,
                        JUDGE_VERSION, JUDGE_CONFIG_FINGERPRINT,
                    ):
                        inserted += 1
            db.commit()
            return inserted

    def enqueue_recheck(self, run_id: str) -> dict[str, int]:
        """Create fresh detector jobs for one run, ignoring prior reports."""

        settings = review_settings()
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            row = db.execute(
                "SELECT response_json FROM react_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or not row["response_json"]:
                raise KeyError(run_id)
            deterministic = self._insert_job(
                db, run_id, "deterministic", RECHECK_PRIORITY, CHECK_VERSION,
                f"{DETERMINISTIC_CONFIG_FINGERPRINT}:recheck:{uuid4().hex[:10]}",
            )
            judge = 0
            if settings.judge_enabled:
                judge = self._insert_job(
                    db, run_id, "judge", RECHECK_PRIORITY, JUDGE_VERSION,
                    f"{JUDGE_CONFIG_FINGERPRINT}:recheck:{uuid4().hex[:10]}",
                )
            db.commit()
        return {"deterministic_jobs": int(deterministic), "judge_jobs": int(judge)}

    def run_once(self, *, limit: int = 10, should_stop: Callable[[], bool] | None = None) -> dict[str, int]:
        """Backfill signals, enqueue detectors, process jobs and aggregate."""

        settings = review_settings()
        if not settings.enabled:
            return {"sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0}
        self.signals.backfill_downvotes(limit=100)
        self.enqueue_deterministic_recent()
        self.enqueue_judge_candidates()
        sampled = self.sample_recent_runs()
        processed = 0
        for _ in range(max(1, min(limit, 50))):
            if should_stop is not None and should_stop():
                break
            job = self._claim_job()
            if job is None:
                break
            try:
                self._process_job(job)
            except Exception as error:  # noqa: BLE001 - job failure is data
                self._fail_job(job, error)
            processed += 1
        aggregated = self.cases.aggregate_signals(limit=100)
        self.cleanup_retention()
        return {
            "sampled_runs": sampled,
            "processed_jobs": processed,
            "aggregated_signals": aggregated,
        }

    def cleanup_retention(self) -> dict[str, int]:
        """Delete aged terminal review data while keeping open cases."""

        settings = review_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            feedback = db.execute(
                "DELETE FROM answer_feedback WHERE updated_at < ?",
                (cutoff,),
            )
            cases = db.execute(
                """
                DELETE FROM answer_review_cases
                WHERE status IN ('closed','dismissed','insufficient')
                  AND updated_at < ?
                """,
                (cutoff,),
            )
            signals = db.execute(
                """
                DELETE FROM answer_review_signals AS signal
                WHERE signal.created_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM answer_review_cases AS case_row
                      WHERE case_row.case_id = signal.case_id
                        AND case_row.status NOT IN ('closed','dismissed','insufficient')
                  )
                """,
                (cutoff,),
            )
            jobs = db.execute(
                """
                DELETE FROM answer_review_jobs
                WHERE status IN ('succeeded','skipped','failed')
                  AND finished_at < ?
                """,
                (cutoff,),
            )
            db.commit()
        return {
            "feedback": int(feedback.rowcount),
            "cases": int(cases.rowcount),
            "signals": int(signals.rowcount),
            "jobs": int(jobs.rowcount),
        }

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[ReviewJob]:
        """Return recent jobs for queue health checks."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            if status:
                rows = db.execute(
                    """
                    SELECT * FROM answer_review_jobs
                    WHERE status=?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM answer_review_jobs ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
        return [_job_from_row(row) for row in rows]

    def _insert_sample_job(self, db, run_id: str) -> bool:
        return self._insert_job(
            db, run_id, "sample", SAMPLE_PRIORITY,
            SAMPLE_DETECTOR_VERSION, SAMPLE_CONFIG_FINGERPRINT,
        )

    def _insert_job(
        self,
        db,
        run_id: str,
        kind: str,
        priority: int,
        detector_version: str,
        config_fingerprint: str,
    ) -> bool:
        result = db.execute(
            """
            INSERT INTO answer_review_jobs (
                job_id, run_id, kind, priority, detector_version, config_fingerprint
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                "job_" + uuid4().hex,
                run_id,
                kind,
                priority,
                detector_version,
                config_fingerprint,
            ),
        )
        return result.rowcount > 0

    def _claim_job(self):
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            row = db.execute(
                """
                UPDATE answer_review_jobs SET
                    status='processing', attempts=attempts+1, locked_by=?,
                    locked_until=clock_timestamp()+? * interval '1 second'
                WHERE job_id = (
                    SELECT job_id FROM answer_review_jobs
                    WHERE (status='pending' AND available_at<=clock_timestamp())
                       OR (status='processing' AND locked_until<=clock_timestamp())
                    ORDER BY priority, created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (self.worker_id, JOB_LEASE_SECONDS),
            ).fetchone()
            db.commit()
            return dict(row) if row else None

    def _process_job(self, job: dict[str, Any]) -> None:
        if job["kind"] == "sample":
            self._process_sample(job)
        elif job["kind"] == "deterministic":
            self._process_deterministic(job)
        elif job["kind"] == "judge":
            self._process_judge(job)
        else:
            self._finish_job(job, "skipped", {"reason": "unknown_job_kind"})

    def _process_sample(self, job: dict[str, Any]) -> None:
        recorded = self.signals.record(
            run_id=job["run_id"],
            kind="sample",
            severity="info",
            detector=SAMPLE_DETECTOR,
            detector_version=SAMPLE_DETECTOR_VERSION,
            source_ref=job["job_id"],
            summary="随机抽检",
            details={"job_id": job["job_id"]},
        )
        self._finish_job(job, "succeeded", {"signal_recorded": recorded})

    def _process_deterministic(self, job: dict[str, Any]) -> None:
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            snapshot = load_run_snapshot(db, job["run_id"])
        if snapshot is None:
            self._finish_job(job, "skipped", {"reason": "snapshot_missing"})
            return
        report = run_deterministic_checks(snapshot)
        failures = report.failures()
        if failures:
            severity = "high" if any(
                item.check in {"tool_evidence", "answer_present", "missing_data_disclosure"}
                for item in failures
            ) else "medium"
            fingerprint = hashlib.sha256(json.dumps(
                [{"check": item.check, "summary": item.summary} for item in failures],
                ensure_ascii=False,
            ).encode("utf-8")).hexdigest()[:16]
            self.signals.record(
                run_id=job["run_id"],
                kind="deterministic",
                severity=severity,
                detector="deterministic",
                detector_version=CHECK_VERSION,
                source_ref="deterministic:" + fingerprint,
                summary="确定性检查失败：" + "；".join(
                    item.summary for item in failures
                )[:400],
                details={"checks": [item.as_dict() for item in failures]},
            )
        self._finish_job(job, "succeeded", {"report": report.as_dict()})

    def _process_judge(self, job: dict[str, Any]) -> None:
        settings = review_settings()
        provider = get_review_judge_provider()
        if provider is None:
            self._finish_job(job, "skipped", {"reason": "judge_disabled"})
            return
        last_error: Exception | None = None
        for _ in range(settings.judge_max_calls_per_job):
            if self.judge_ledger.calls_today() >= settings.judge_daily_call_limit:
                self._defer_job(job, "judge_daily_call_limit")
                return
            with closing(connect(self.database_url)) as db:
                initialize_database(db)
                snapshot = load_run_snapshot(db, job["run_id"])
            if snapshot is None:
                self._finish_job(job, "skipped", {"reason": "snapshot_missing"})
                return
            try:
                outcome = run_judge(
                    provider, snapshot,
                    max_input_chars=settings.judge_max_input_chars,
                )
            except JudgeUnavailable as error:
                last_error = error
                self.judge_ledger.record(
                    job_id=job["job_id"],
                    run_id=job["run_id"],
                    status="error",
                    error_type=type(error).__name__,
                )
                continue
            self.judge_ledger.record(
                job_id=job["job_id"],
                run_id=job["run_id"],
                status="succeeded",
                outcome=outcome,
            )
            report = outcome.report
            result = {
                "report": {
                    "detector": "judge",
                    "version": JUDGE_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "overall": report.overall,
                    "dimensions": [item.model_dump() for item in report.dimensions],
                    "notes": report.notes,
                },
            }
            if report.overall == "fail":
                failed = [item for item in report.dimensions if item.verdict == "fail"]
                severity = "high" if any(
                    item.dimension in {"factual_grounding", "compliance"}
                    for item in failed
                ) else "medium"
                fingerprint = hashlib.sha256(json.dumps(
                    [{"dimension": item.dimension, "reason": item.reason} for item in failed],
                    ensure_ascii=False,
                ).encode("utf-8")).hexdigest()[:16]
                self.signals.record(
                    run_id=job["run_id"],
                    kind="judge",
                    severity=severity,
                    detector="judge",
                    detector_version=JUDGE_VERSION,
                    source_ref="judge:" + fingerprint,
                    summary="模型评审失败：" + "；".join(
                        f"{item.dimension}: {item.reason}" for item in failed
                    )[:400],
                    details=result["report"],
                )
            self._finish_job(job, "succeeded", result)
            return
        if self.judge_ledger.calls_today() >= settings.judge_daily_call_limit:
            self._defer_job(job, "judge_daily_call_limit")
            return
        self._finish_job(
            job, "failed",
            {"error": type(last_error).__name__ if last_error else "judge_unavailable"},
        )

    def _finish_job(self, job: dict[str, Any], status: str, result: dict[str, Any]) -> None:
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            db.execute(
                """
                UPDATE answer_review_jobs
                SET status=?, result_json=?, error_message=NULL,
                    finished_at=clock_timestamp(), locked_by=NULL, locked_until=NULL
                WHERE job_id=?
                """,
                (status, json.dumps(result, ensure_ascii=False), job["job_id"]),
            )
            db.commit()

    def _defer_job(self, job: dict[str, Any], reason: str) -> None:
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            db.execute(
                """
                UPDATE answer_review_jobs
                SET status='pending', available_at=?, result_json=?,
                    error_message=NULL, locked_by=NULL, locked_until=NULL
                WHERE job_id=?
                """,
                (
                    shanghai_next_day_start().isoformat(),
                    json.dumps({"deferred": reason}, ensure_ascii=False),
                    job["job_id"],
                ),
            )
            db.commit()

    def _fail_job(self, job: dict[str, Any], error: Exception) -> None:
        attempts = int(job.get("attempts") or 1)
        if attempts < MAX_ATTEMPTS:
            with closing(connect(self.database_url)) as db:
                initialize_database(db)
                begin_write(db)
                db.execute(
                    """
                    UPDATE answer_review_jobs
                    SET status='pending',
                        available_at=clock_timestamp()+? * interval '1 second',
                        error_message=?, locked_by=NULL, locked_until=NULL
                    WHERE job_id=?
                    """,
                    (RETRY_DELAY_SECONDS * attempts, type(error).__name__, job["job_id"]),
                )
                db.commit()
            return
        self._finish_job(job, "failed", {"error": type(error).__name__})


def _job_from_row(row) -> ReviewJob:
    return ReviewJob(
        job_id=row["job_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        priority=int(row["priority"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        available_at=_datetime(row["available_at"]),
        detector_version=row["detector_version"],
        config_fingerprint=row["config_fingerprint"],
        error_message=row["error_message"],
        created_at=_datetime(row["created_at"]),
        finished_at=_datetime(row["finished_at"]) if row["finished_at"] else None,
    )


def _datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
