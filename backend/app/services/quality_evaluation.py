"""Run bounded deterministic assessments against immutable evidence packages."""

from contextlib import contextmanager
import hashlib
import math
import os
import threading
from time import perf_counter
from uuid import uuid4

from app.quality_evaluation_models import EVALUATOR_VERSION, EvaluationReport
from app.quality_models import QualityConflict
from app.services.quality_checks import evaluate_bundle
from app.services.quality_detection import retention_days
from app.services.quality_trace import prune_trace_snapshots


def evaluation_timeout():
    value = float(os.getenv("LIMITUPLAB_QUALITY_EVALUATION_TIMEOUT_SECONDS", "30"))
    if not math.isfinite(value) or not 1 <= value <= 300:
        raise ValueError("Evaluation timeout must be between 1 and 300 seconds")
    return value


@contextmanager
def held_lease(repository, job, *, lease_seconds=60):
    """Keep ownership alive; the final transaction still checks the lease token."""
    stop = threading.Event()
    lost = threading.Event()

    def renew():
        while not stop.wait(lease_seconds / 3):
            try:
                repository.renew_job(job, lease_seconds=lease_seconds)
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True, name="quality-lease")
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=1)


def report_signals(job, report):
    """Signals request human review; they never set a human quality verdict."""
    if report.execution_status != "succeeded":
        return []
    signals = []
    response = job["bundle"].get("response") or {}

    def add(code, summary, evidence, severity="low", source="deterministic"):
        signals.append({"source": source, "code": code, "severity": severity,
            "summary": summary, "evidence": {**evidence, "quality_verdict": "unknown",
                "bundle_id": job["bundle_id"]}, "detector_version": job["detector_version"]})

    for check in report.checks:
        if check.verdict == "fail":
            add(check.check_id, check.summary, {**check.evidence, "status": "fail",
                "dimension": check.dimension}, severity=check.severity)
    status = response.get("task_status")
    if status in {"partial", "cancelled"}:
        add("incomplete_run", "运行部分完成或被取消，记录线索供人工判断。", {
            "status": "unknown", "task_status": status, "stop_reason": response.get("stop_reason")})
    observation = job.get("transport_observation") or {}
    receipts = observation.get("delivery_events") or []
    digest = job["answer_hash"]
    acknowledged = any(event.get("answer_hash") == digest and event.get("event") in {"received", "displayed"}
                       for event in receipts)
    interrupted = [event for event in receipts if event.get("event") == "interrupted"
                   and event.get("answer_hash") in {"", digest}]
    if interrupted and not acknowledged and status != "cancelled":
        add("interrupted", "客户端报告交付中断，尚无对应答案的接收或展示回执。", {
            "status": "unknown", "observation": observation}, source="delivery")
    rate = float(job["config"].get("normal_sample_rate", 0.05))
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("Invalid frozen sampling rate")
    if not signals and status in {"complete", "empty", "clarify", "refuse"}:
        bucket = int(hashlib.sha256(f"{job['run_id']}:{job['detector_version']}".encode()).hexdigest()[:16], 16) / 2**64
        if bucket < rate:
            add("sampled_run", "正常结束的运行进入随机抽样，质量尚待人工复核。", {
                "status": "unknown", "sampling_method": "sha256_run_id_policy", "sample_rate": rate,
                "sampling_bucket": bucket, "policy_version": job["detector_version"]}, source="normal_sample")
    return signals


def process_evaluations(repository=None, *, limit=20, stop_event=None):
    from app.repositories.quality_repository import QualityRepository

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    timeout, days = evaluation_timeout(), retention_days()
    repository = repository or QualityRepository()
    result = {"processed": 0, "succeeded": 0, "failed": 0,
              "backfilled": repository.backfill_jobs(limit=max(100, limit)), "pruned": 0}
    worker = "quality-" + uuid4().hex
    for _ in range(limit):
        if stop_event is not None and stop_event.is_set():
            break
        job = repository.claim_job(worker, job_kind="deterministic", evaluator_version=EVALUATOR_VERSION)
        if job is None:
            break
        result["processed"] += 1
        started = perf_counter()
        try:
            with held_lease(repository, job) as lost:
                report = EvaluationReport.model_validate(evaluate_bundle(job["bundle"], deadline=started + timeout))
                if lost.is_set():
                    raise QualityConflict("Evaluation lease was lost")
                if perf_counter() > started + timeout:
                    raise TimeoutError("Deterministic evaluation exceeded its budget")
                report.metadata.update(duration_ms=round((perf_counter() - started) * 1000),
                    model_calls=0, model_tokens=0, transport_observation=job.get("transport_observation"),
                    sampling_config=job["config"])
                repository.complete_job(job, report_signals(job, report), report=report.model_dump())
            result["succeeded"] += 1
        except Exception as error:
            try:
                repository.fail_job(job, type(error).__name__, report=EvaluationReport(
                    execution_status="error", verdict="unknown", metadata={
                        "error_type": type(error).__name__, "duration_ms": round((perf_counter() - started) * 1000),
                        "model_calls": 0, "model_tokens": 0,
                    }).model_dump())
            except QualityConflict:
                pass
            result["failed"] += 1
    with repository.connection() as db:
        result["pruned"] = prune_trace_snapshots(db, retention_days=days)
    return result
