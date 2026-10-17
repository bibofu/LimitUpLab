"""Consume only explicitly requested semantic reviews, outside chat requests."""

import hashlib
import json
from time import perf_counter
from uuid import uuid4

from app.quality_evaluation_models import EvaluationReport
from app.quality_judge_models import JUDGE_VERSION
from app.quality_models import QualityConflict
from app.repositories.quality_repository import QualityRepository
from app.repositories.quality_judge_repository import JudgeBudgetExceeded
from app.services.llm_provider import capture_llm_usage
from app.services.quality_evaluation import held_lease
from app.services.quality_judge import JudgeEvaluationError, evaluate_judge
from app.services.quality_judge_config import JudgeConfiguration, create_judge_provider, judge_configuration, judge_status


class AuditedJudgeProvider:
    def __init__(self, repository, job, provider, config, limits, lost):
        self.repository, self.job, self.provider = repository, job, provider
        self.config, self.limits, self.lost = config, limits, lost
        self.phase = None
        self.calls = []

    def before_call(self, phase):
        if self.lost.is_set():
            raise QualityConflict("Judge lease was lost")
        self.phase = phase

    def generate_messages(self, messages, tools, **kwargs):
        if self.phase not in {"initial", "repair"} or self.lost.is_set():
            raise QualityConflict("Judge request has no current authorization")
        phase, self.phase = self.phase, None
        request = {"messages": [{"role": item.type, "content": item.content} for item in messages],
                   "tools": tools, "parameters": kwargs, "model": self.config.model}
        digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        call = self.repository.reserve_judge_call(self.job, uuid4().hex,
            daily_limit=min(self.config.daily_call_limit, self.limits.daily_call_limit),
            job_limit=min(self.config.max_calls_per_job, self.limits.max_calls_per_job),
            max_concurrency=self.limits.max_concurrency, timeout_seconds=self.config.timeout_seconds,
            request_hash=digest, phase=phase)
        self.repository.start_judge_call(self.job, call["call_id"])
        started = perf_counter()
        audit = {"call_id": call["call_id"], "phase": phase, "request_hash": digest,
                 "status": "error", "usage": None, "error_type": None}
        self.calls.append(audit)
        tracker = None
        try:
            with capture_llm_usage() as tracker:
                result = self.provider.generate_messages(messages, tools, **kwargs)
            if tracker.token_usage_complete:
                audit["usage"] = {"prompt_tokens": tracker.prompt_tokens, "completion_tokens": tracker.completion_tokens,
                                  "total_tokens": tracker.total_tokens}
            else:
                usage = getattr(result, "usage_metadata", None) or {}
                audit["usage"] = {name: usage.get(key) for name, key in (
                    ("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"), ("total_tokens", "total_tokens"))}
            audit["status"] = "succeeded"
            return result
        except BaseException as error:
            audit["error_type"] = type(error).__name__
            if tracker is not None and tracker.token_usage_complete:
                audit["usage"] = {"prompt_tokens": tracker.prompt_tokens, "completion_tokens": tracker.completion_tokens,
                                  "total_tokens": tracker.total_tokens}
            raise
        finally:
            audit["duration_ms"] = round((perf_counter() - started) * 1000)
            self.repository.finish_judge_call(call["call_id"], status=audit["status"], usage=audit["usage"],
                                              error_type=audit["error_type"], duration_ms=audit["duration_ms"])

    def metadata(self):
        counts = [(item.get("usage") or {}).get("total_tokens") for item in self.calls]
        return {"model_calls": len(self.calls), "model_tokens": sum(counts) if all(type(n) is int for n in counts) else None,
                "usage_complete": all(type(n) is int for n in counts), "calls": self.calls}


def judge_signals(report, job):
    if report.execution_status != "succeeded":
        return []
    return [{"source": "llm_judge", "code": "judge:" + check.dimension, "severity": check.severity,
             "summary": check.summary[:1000], "evidence": {**check.evidence, "dimension": check.dimension,
                 "status": "suspected", "quality_verdict": "unknown", "bundle_id": job["bundle_id"],
                 "evaluation_id": job["evaluation_id"]}, "detector_version": JUDGE_VERSION}
            for check in report.checks if check.verdict == "fail"]


def process_judge_jobs(repository=None, *, limit=5, stop_event=None, provider_factory=create_judge_provider):
    if not 1 <= limit <= 20:
        raise ValueError("Judge batch limit must be between 1 and 20")
    result = {"processed": 0, "succeeded": 0, "failed": 0, "deferred": 0, "enabled": judge_status()["enabled"]}
    if not result["enabled"]:
        return result
    limits = judge_configuration()
    repository = repository or QualityRepository()
    for _ in range(limit):
        if stop_event is not None and stop_event.is_set():
            break
        job = repository.claim_job("judge-" + uuid4().hex, job_kind="llm_judge", evaluator_version=JUDGE_VERSION,
                                   max_concurrency=limits.max_concurrency)
        if job is None:
            break
        result["processed"] += 1
        started, audited = perf_counter(), None
        try:
            with held_lease(repository, job) as lost:
                if job["config"].get("manual_trigger") is not True:
                    raise ValueError("Judge jobs require an explicit manual trigger")
                config = JudgeConfiguration.model_validate(job["config"])
                audited = AuditedJudgeProvider(repository, job, provider_factory(config), config, limits, lost)
                report = EvaluationReport.model_validate(evaluate_judge(job["bundle"], provider=audited,
                    config=config.model_dump(mode="json"), before_call=audited.before_call))
                if lost.is_set():
                    raise QualityConflict("Judge lease was lost")
                report.metadata.update(audited.metadata())
                report.metadata.update(model=config.model, manual_only=True, configuration=job["config"],
                                       duration_ms=round((perf_counter() - started) * 1000))
                repository.complete_job(job, judge_signals(report, job), report=report.model_dump(mode="json"))
                result["succeeded"] += 1
        except JudgeBudgetExceeded as error:
            metadata = dict(getattr(error, "metadata", {}))
            metadata.update(audited.metadata() if audited else {})
            try:
                repository.defer_judge_job(job, error.reason, metadata=metadata)
            except QualityConflict:
                pass
            result["deferred"] += 1
        except Exception as error:
            metadata = dict(error.metadata) if isinstance(error, JudgeEvaluationError) else {}
            if isinstance(error, JudgeEvaluationError):
                metadata["error_code"] = error.code
            metadata.update(audited.metadata() if audited else {"model_calls": 0, "model_tokens": None})
            metadata.update(error_type=type(error).__name__, manual_only=True,
                            duration_ms=round((perf_counter() - started) * 1000))
            try:
                repository.fail_job(job, type(error).__name__, report=EvaluationReport(evaluator_kind="llm_judge",
                    evaluator_version=JUDGE_VERSION, execution_status="error", verdict="unknown", metadata=metadata).model_dump())
            except QualityConflict:
                pass
            result["failed"] += 1
    return result
