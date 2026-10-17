"""Deterministic review candidates; never interpret prose or assign a quality verdict."""

import hashlib
import math
import os
from uuid import uuid4

from app.quality_models import DetectionSignal, QualityConflict
from app.quality_schema import DETECTOR_VERSION, answer_hash


def sample_rate():
    value = float(os.getenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", "0.05"))
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE must be between 0 and 1")
    return value


def retention_days():
    value = int(os.getenv("LIMITUPLAB_QUALITY_TRACE_RETENTION_DAYS", "30"))
    if not 1 <= value <= 3650:
        raise ValueError("LIMITUPLAB_QUALITY_TRACE_RETENTION_DAYS must be between 1 and 3650")
    return value


def _signal(code, summary, evidence, *, severity="medium", source="deterministic"):
    return DetectionSignal(source=source, code=code, severity=severity, summary=summary,
                           evidence=evidence, detector_version=DETECTOR_VERSION).model_dump()


def detect_run(job, *, rate=None):
    """Inspect full persisted structures. Absent old evidence is unknown, not failure."""
    rate = sample_rate() if rate is None else rate
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("Sample rate must be between 0 and 1")
    response = job.get("response") or {}
    status = response.get("task_status")
    signals = []
    if status == "error":
        signals.append(_signal("terminal_error", "运行最终失败，需复核执行原因。", {
            "status": "fail", "path": "response.task_status", "task_status": status,
            "stop_reason": response.get("stop_reason"), "quality_verdict": "unknown",
        }))
    elif status in {"partial", "cancelled"}:
        signals.append(_signal("incomplete_run", "运行部分完成或被取消，记录线索供人工判断。", {
            "status": "unknown", "path": "response.task_status", "task_status": status,
            "stop_reason": response.get("stop_reason"), "quality_verdict": "unknown",
        }, severity="low"))

    digest = answer_hash(response) if response else None
    delivery_events = job.get("delivery_events") or []
    acknowledged = digest is not None and any(
        event.get("answer_hash") == digest and event.get("event") in {"received", "displayed"}
        for event in delivery_events)
    interrupted = [event for event in delivery_events if event.get("event") == "interrupted"
                   and event.get("answer_hash") in {"", digest}]
    if interrupted and not acknowledged and status != "cancelled":
        signals.append(_signal("interrupted", "客户端报告交付中断，尚无对应答案的接收或展示回执。", {
            "status": "unknown", "quality_verdict": "unknown", "path": "delivery_events",
            "events": interrupted,
        }, severity="low", source="delivery"))

    checkpoint = job.get("checkpoint") or {}
    runtime, state = checkpoint.get("runtime", {}), checkpoint.get("state", {})
    evidence = runtime.get("evidence")
    # Legacy serialized EvidenceStore objects wrap their records.
    if isinstance(evidence, dict) and "records" in evidence:
        evidence = evidence["records"]
    final = state.get("finish")
    # A rejected candidate or recovered fallback is not the delivered answer.
    delivered_finish = (response.get("stop_reason") == "answered"
                        and runtime.get("status") == status
                        and state.get("done") is True
                        and runtime.get("answer") == response.get("answer")
                        and isinstance(final, dict) and final.get("status") == status
                        and isinstance(evidence, dict))
    if delivered_finish:
        references = list(final.get("evidence_ids") or [])
        table = final.get("table")
        if isinstance(table, dict) and table.get("evidence_id"):
            references.append(table["evidence_id"])
        invalid = []
        for key in dict.fromkeys(references):
            record = evidence.get(key)
            if (not isinstance(record, dict) or record.get("historical_reference")
                    or record.get("evidence_scope") == "conversation_history"):
                invalid.append(key)
        if invalid:
            signals.append(_signal("invalid_evidence_reference", "已交付结构化结果引用了缺失或非本轮证据。", {
                "status": "fail", "path": "checkpoint.state.finish.evidence_ids", "evidence_ids": invalid,
            }))
        signals.extend(_check_rows(evidence, references))

    if not signals and status in {"complete", "empty", "clarify", "refuse"}:
        bucket = int(hashlib.sha256(f"{job['run_id']}:{DETECTOR_VERSION}".encode()).hexdigest()[:16], 16) / 2**64
        if bucket < rate:
            signals.append(_signal("sampled_run", "正常结束的运行进入随机抽样，质量尚待人工复核。", {
                "status": "unknown", "sampling_method": "sha256_run_id_policy",
                "sample_rate": rate, "sampling_bucket": bucket, "policy_version": DETECTOR_VERSION,
            }, severity="low", source="normal_sample"))
    return signals


def _check_rows(evidence, references):
    signals = []
    count_tools = {"limit_up_events", "market_event_pool", "remote_limit_up_pool", "dragon_tiger_list", "compute_result"}
    for key in dict.fromkeys(references):
        record = evidence.get(key)
        if not isinstance(record, dict) or record.get("result_state") == "error":
            continue
        rows, payload = record.get("rows"), record.get("payload")
        if not isinstance(rows, list) or not isinstance(payload, dict):
            continue
        count = payload.get("returned_count")
        if record.get("tool") in count_tools and type(count) is int and count != len(rows):
            signals.append(_signal("evidence_count_mismatch:" + key, "完整证据的返回条数与实际行数不一致。", {
                "status": "fail", "evidence_id": key, "declared_returned_count": count,
                "actual_row_count": len(rows), "path": "checkpoint.runtime.evidence." + key,
            }))
        # Only this tool's event contract guarantees a stock/date identity.
        # A stock appearing on two dates or on two Dragon-Tiger reasons is valid.
        if record.get("tool") == "limit_up_events" and rows and all(
            isinstance(row, dict) and isinstance(row.get("symbol"), str)
            and isinstance(row.get("trade_date"), str) for row in rows
        ):
            identities = [(row["symbol"], row["trade_date"]) for row in rows]
            if len(set(identities)) != len(identities):
                signals.append(_signal("duplicate_rows:" + key, "涨停事件中同一股票同一交易日出现重复记录。", {
                    "status": "fail", "evidence_id": key, "identity_fields": ["symbol", "trade_date"],
                    "row_count": len(rows), "unique_identity_count": len(set(identities)),
                }))
    return signals


def process_pending(repository=None, *, limit=20, stop_event=None):
    """Backfill, claim with a lease, and commit candidates atomically per job."""
    from app.repositories.quality_repository import QualityRepository
    from app.services.quality_trace import prune_trace_snapshots

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    rate, days = sample_rate(), retention_days()
    repository = repository or QualityRepository()
    result = {"processed": 0, "succeeded": 0, "failed": 0,
              "backfilled": repository.backfill_jobs(limit=max(100, limit)), "pruned": 0}
    worker = "quality-" + uuid4().hex
    for _ in range(limit):
        if stop_event is not None and stop_event.is_set():
            break
        job = repository.claim_job(worker, lease_seconds=60)
        if job is None:
            break
        result["processed"] += 1
        try:
            repository.complete_job(job, detect_run(job, rate=rate))
            result["succeeded"] += 1
        except Exception as error:
            # Persist only the error class; exception strings can contain user data.
            try:
                repository.fail_job(job, type(error).__name__)
            except QualityConflict:
                pass  # The expired lease is now owned by another worker.
            result["failed"] += 1
    with repository.connection() as db:
        result["pruned"] = prune_trace_snapshots(db, retention_days=days)
    return result
