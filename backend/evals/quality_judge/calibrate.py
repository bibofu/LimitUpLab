"""Explicit offline diagnostics; never imported by the production Judge worker."""

import json
from pathlib import Path

from app.quality_judge_models import DIMENSIONS, JudgeEvaluationError
from app.services.llm_provider import capture_llm_usage
from app.services.quality_evidence import build_evidence_bundle, content_hash
from app.services.quality_judge import evaluate_judge, judge_identity


DEFAULT_SAMPLES = Path(__file__).with_name("samples.json")


def load_samples(path=DEFAULT_SAMPLES, *, split=None):
    """A caller can supply separately human-labelled data with explicit provenance."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != "quality-judge-calibration-v1":
        raise ValueError("Unknown calibration sample schema")
    if not isinstance(document.get("human_calibrated"), bool) or not document.get("provenance"):
        raise ValueError("Calibration data requires explicit provenance")
    selected, identifiers = [], set()
    for sample in document.get("samples", []):
        identity = sample.get("id")
        if not isinstance(identity, str) or not identity or identity in identifiers:
            raise ValueError("Sample ids must be unique")
        identifiers.add(identity)
        if sample.get("split") not in {"development", "holdout"}:
            raise ValueError("Sample split must be explicit")
        if not sample.get("question") or not sample.get("answer"):
            raise ValueError("Each sample needs its visible question and answer")
        expected = sample.get("expected")
        if not isinstance(expected, dict) or not expected or any(
            key not in DIMENSIONS or value not in {"pass", "fail", "unknown"} for key, value in expected.items()
        ):
            raise ValueError("Expected dimension verdicts are invalid")
        if split is None or sample["split"] == split:
            selected.append(sample)
    if split is not None and split not in {"development", "holdout"}:
        raise ValueError("Unknown split")
    return {"samples": selected, "sample_set_hash": content_hash(document),
            "provenance": document["provenance"], "human_calibrated": document["human_calibrated"]}


def sample_bundle(sample):
    evidence = {"sample_evidence": {"tool": sample.get("tool", "recorded_statistics"),
                "schema_version": "react-evidence-v4", "payload": sample.get("payload", {})}}
    return build_evidence_bundle({"run_id": "calibration-" + sample["id"], "session_id": "offline-calibration",
        "started_at": "2026-10-17T00:00:00+00:00", "request": {"message": sample["question"]},
        "response": {"answer": sample["answer"], "task_status": "complete"},
        "checkpoint": {"runtime": {"evidence": evidence}, "state": {"finish": {"evidence_ids": ["sample_evidence"]}}},
        "context": sample.get("context", []), "calls": [], "diagnostic_snapshots": [],
        "assistant_messages": [], "delivery_events": []})


def diagnostic_metrics(records):
    counts = {"samples": len(records), "execution_failures": 0, "comparisons": 0,
              "matched": 0, "false_fail": 0, "missed_fail": 0, "unknown": 0}
    for record in records:
        report = record.get("report") or {}
        if report.get("execution_status") != "succeeded":
            counts["execution_failures"] += 1
            continue
        actual = {check["dimension"]: check["verdict"] for check in report["checks"]}
        for dimension, expected in record["expected"].items():
            observed = actual.get(dimension, "unknown")
            counts["comparisons"] += 1
            counts["matched"] += int(observed == expected)
            counts["false_fail"] += int(observed == "fail" and expected != "fail")
            counts["missed_fail"] += int(observed != "fail" and expected == "fail")
            counts["unknown"] += int(observed == "unknown")
    return counts


def run_calibration(provider, *, sample_path=DEFAULT_SAMPLES, split=None, config=None, max_calls=24):
    """Explicit invocation only; hard cap includes repairs. Returns an in-memory report."""
    if type(max_calls) is not int or not 1 <= max_calls <= 50:
        raise ValueError("Calibration call cap must be between 1 and 50")
    samples = load_samples(sample_path, split=split)
    records, call_count = [], 0

    def reserve(phase):
        nonlocal call_count
        if call_count >= max_calls:
            raise RuntimeError("offline_calibration_call_limit")
        call_count += 1

    for sample in samples["samples"]:
        record = {"id": sample["id"], "split": sample["split"], "expected": sample["expected"]}
        with capture_llm_usage() as usage:
            try:
                record["report"] = evaluate_judge(sample_bundle(sample), provider=provider,
                    config=config or {}, before_call=reserve)
            except JudgeEvaluationError as error:
                record["report"] = {"execution_status": "error", "verdict": "unknown", "checks": [],
                                    "metadata": {**error.metadata, "error_code": error.code}}
        record["usage"] = {"model_calls": usage.call_count,
            "total_tokens": usage.total_tokens if usage.token_usage_complete else None}
        records.append(record)
    return {"schema_version": "quality-judge-calibration-result-v1", "judge_identity": judge_identity(),
            "sample_set_hash": samples["sample_set_hash"], "provenance": samples["provenance"],
            "human_calibrated": samples["human_calibrated"], "model": getattr(provider, "model", None),
            "call_count": call_count, "metrics": diagnostic_metrics(records), "records": records,
            "scope": "Small engineering diagnostic set; these metrics do not estimate production accuracy."}
