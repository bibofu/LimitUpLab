"""Review sampling, job claiming and the background review worker."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import review_settings
from app.database import begin_write, connect, initialize_database
from app.review_models import ReviewJob
from app.services.review_cases import ReviewCaseService
from app.services.review_signals import ReviewSignalService


SHANGHAI = ZoneInfo("Asia/Shanghai")
JOB_LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30
SAMPLE_DETECTOR = "random_sample"
SAMPLE_DETECTOR_VERSION = "sample-v1"
SAMPLE_CONFIG_FINGERPRINT = "sample-lane-v1"
SAMPLE_PRIORITY = 200


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
        self.worker_id = "review-worker-" + uuid4().hex[:12]

    def sample_recent_runs(self, *, limit: int = 200) -> int:
        """Enqueue the configured share of recent completed answers."""

        settings = review_settings()
        if not settings.enabled or settings.sample_rate <= 0 or settings.sample_daily_limit <= 0:
            return 0
        day_start = _shanghai_day_start()
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

    def run_once(self, *, limit: int = 10) -> dict[str, int]:
        """Backfill signals, enqueue samples, process jobs and aggregate cases."""

        settings = review_settings()
        if not settings.enabled:
            return {"sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0}
        self.signals.backfill_downvotes(limit=100)
        sampled = self.sample_recent_runs()
        processed = 0
        for _ in range(max(1, min(limit, 50))):
            job = self._claim_job()
            if job is None:
                break
            try:
                self._process_job(job)
            except Exception as error:  # noqa: BLE001 - job failure is data
                self._fail_job(job, error)
            processed += 1
        aggregated = self.cases.aggregate_signals(limit=100)
        return {
            "sampled_runs": sampled,
            "processed_jobs": processed,
            "aggregated_signals": aggregated,
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
        result = db.execute(
            """
            INSERT INTO answer_review_jobs (
                job_id, run_id, kind, priority, detector_version, config_fingerprint
            )
            VALUES (?, ?, 'sample', ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                "job_" + uuid4().hex,
                run_id,
                SAMPLE_PRIORITY,
                SAMPLE_DETECTOR_VERSION,
                SAMPLE_CONFIG_FINGERPRINT,
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
        if job["kind"] != "sample":
            self._finish_job(job, "skipped", {"reason": "detector_not_implemented"})
            return
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


def _shanghai_day_start() -> datetime:
    now = datetime.now(SHANGHAI)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


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
