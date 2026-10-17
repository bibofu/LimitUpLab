"""Candidate detection uses complete structured evidence, never answer text."""

import copy
import importlib.util
from pathlib import Path

import pytest

from app.services import quality_detection as detection
from app.quality_schema import answer_hash


def job(status="complete", *, final=None, evidence=None):
    return {"run_id": "run_fixture", "response": {
        "task_status": status, "stop_reason": "answered", "answer": "000001 出现两次，数量 999。",
    }, "checkpoint": {"runtime": {"status": status, "evidence": evidence or {},
                                   "answer": "000001 出现两次，数量 999。"},
                       "state": {"done": True, "finish": final or {"status": status, "evidence_ids": []}}}}


def test_recovered_failure_does_not_mark_answer_bad():
    run = job()
    run["response"]["tool_results"] = [{"name": "failed_source", "status": "error"},
                                       {"name": "replacement", "status": "success"}]
    run["calls"] = [{"result": {"result_state": "error"}}, {"result": {"result_state": "ok"}}]
    assert detection.detect_run(run, rate=0) == []


def test_transport_interruption_without_answer_is_only_unknown_candidate():
    run = {"run_id": "active", "response": None, "delivery_events": [{"event": "interrupted", "answer_hash": ""}]}
    signals = detection.detect_run(run, rate=0)
    assert len(signals) == 1
    assert signals[0]["source"] == "delivery"
    assert signals[0]["severity"] == "low"
    assert signals[0]["evidence"]["quality_verdict"] == "unknown"


@pytest.mark.parametrize("receipt", ["received", "displayed"])
def test_final_receipt_supersedes_run_scoped_interruption(receipt):
    run = job()
    run["delivery_events"] = [{"event": "interrupted", "answer_hash": ""},
                              {"event": receipt, "answer_hash": answer_hash(run["response"])}]
    assert detection.detect_run(run, rate=0) == []
    run["delivery_events"][1]["answer_hash"] = "stale-version"
    assert detection.detect_run(run, rate=0)[0]["code"] == "interrupted"


def test_known_user_cancellation_is_not_a_transport_defect():
    run = job("cancelled")
    run["delivery_events"] = [{"event": "interrupted", "answer_hash": ""}]
    assert [signal["code"] for signal in detection.detect_run(run, rate=0)] == ["incomplete_run"]


@pytest.mark.parametrize("status,code,check", [
    ("error", "terminal_error", "fail"), ("partial", "incomplete_run", "unknown"),
    ("cancelled", "incomplete_run", "unknown"),
])
def test_execution_outcomes_are_candidates_not_quality_verdicts(status, code, check):
    signals = detection.detect_run(job(status), rate=0)
    assert [signal["code"] for signal in signals] == [code]
    assert signals[0]["evidence"]["status"] == check
    assert signals[0]["evidence"]["quality_verdict"] == "unknown"


def test_missing_legacy_checkpoint_is_unknown_and_not_failure():
    run = job(final={"status": "complete", "evidence_ids": ["ev_missing"]})
    run["checkpoint"] = None
    assert detection.detect_run(run, rate=0) == []


def test_rejected_finish_is_not_treated_as_delivered_answer():
    run = job(final={"status": "complete", "evidence_ids": ["ev_missing"]})
    run["response"]["stop_reason"] = "validation_failed"
    assert detection.detect_run(run, rate=0) == []


@pytest.mark.parametrize("field", ["done", "answer"])
def test_incomplete_legacy_snapshot_is_not_evidence_of_invalid_citation(field):
    run = job(final={"status": "complete", "evidence_ids": ["ev_missing"]})
    run["checkpoint"]["state" if field == "done" else "runtime"].pop(field)
    assert detection.detect_run(run, rate=0) == []


def test_delivered_unknown_and_historical_references_are_candidates():
    run = job(final={"status": "complete", "evidence_ids": ["missing", "old"]}, evidence={
        "old": {"evidence_scope": "conversation_history"},
    })
    signals = detection.detect_run(run, rate=0)
    assert len(signals) == 1
    assert signals[0]["code"] == "invalid_evidence_reference"
    assert signals[0]["evidence"]["evidence_ids"] == ["missing", "old"]


def test_duplicate_identity_is_scoped_to_date_and_explicit_contract():
    rows = [{"symbol": "000001", "trade_date": "2026-10-14"},
            {"symbol": "000001", "trade_date": "2026-10-15"}]
    evidence = {"ev_test": {"tool": "limit_up_events", "result_state": "ok",
                           "rows": rows, "payload": {"returned_count": 2, "matched_count": 90}}}
    run = job(final={"status": "complete", "evidence_ids": ["ev_test"]}, evidence=evidence)
    assert detection.detect_run(run, rate=0) == []  # matched_count is not returned_count.
    run["checkpoint"]["runtime"]["evidence"]["ev_test"]["rows"][1]["trade_date"] = "2026-10-14"
    assert detection.detect_run(run, rate=0)[0]["code"] == "duplicate_rows:ev_test"
    run["checkpoint"]["runtime"]["evidence"]["ev_test"]["tool"] = "dragon_tiger_list"
    assert detection.detect_run(run, rate=0) == []  # Multiple listing reasons are legitimate.


def test_full_returned_count_mismatch_uses_structured_rows():
    run = job(final={"status": "complete", "table": {"evidence_id": "ev_test"}}, evidence={
        "ev_test": {"tool": "compute_result", "result_state": "ok", "rows": [{"count": 7}],
                    "payload": {"returned_count": 2}},
    })
    signals = detection.detect_run(run, rate=0)
    assert [signal["code"] for signal in signals] == ["evidence_count_mismatch:ev_test"]
    assert signals[0]["evidence"]["actual_row_count"] == 1


def test_sampling_is_reproducible_records_denominator_and_never_implies_failure():
    original = job()
    first = detection.detect_run(original, rate=1)
    assert first == detection.detect_run(copy.deepcopy(original), rate=1)
    assert first[0]["source"] == "normal_sample"
    assert first[0]["evidence"]["sample_rate"] == 1
    assert first[0]["evidence"]["status"] == "unknown"
    assert detection.detect_run(original, rate=0) == []


@pytest.mark.parametrize("value", ["-1", "1.1", "nan", "inf", "bad"])
def test_sample_rate_configuration_is_validated(monkeypatch, value):
    monkeypatch.setenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", value)
    with pytest.raises(ValueError):
        detection.sample_rate()


def test_worker_once_and_graceful_shutdown_do_not_sleep(monkeypatch, capsys):
    path = Path(__file__).parents[1] / "scripts" / "run_quality_worker.py"
    spec = importlib.util.spec_from_file_location("quality_worker_test", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "configure_runtime_environment", lambda: None)
    monkeypatch.setattr(worker, "sample_rate", lambda: 0)
    monkeypatch.setattr(worker, "retention_days", lambda: 30)
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    calls = []
    def process(**kwargs):
        calls.append(kwargs["limit"])
        kwargs["stop_event"].set()
        return {"processed": 1, "failed": 0}
    monkeypatch.setattr(worker, "process_pending", process)
    assert worker.main(["--once", "--limit", "3"]) == 0
    assert worker.main(["--interval", "60"]) == 0
    assert calls == [3, 20]
    assert '"processed": 1' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        worker.main(["--interval", "nan"])
