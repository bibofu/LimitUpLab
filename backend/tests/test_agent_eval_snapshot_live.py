"""Opt-in real-local integration, never real LLM or production database writes."""

from copy import deepcopy
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

from app.agent_eval.loader import load_case, load_world
from app.agent_eval.snapshot_live import LOCAL_TOOLS, SnapshotLiveRegistry, build_live_suite, baseline_payload
from app.agent_eval.worker import execute_case
from app.agent_eval.historical_live import HistoricalDataDrift
from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.services.prompt_security import PromptInjectionAssessment


@pytest.fixture(autouse=True)
def scripted_safety(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("integration test must not use network")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **k: ComplianceReview(
        decision="allow", violations=[], reason="scripted integration only"))
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k: PromptInjectionAssessment(
        decision="allow", signals=[], reason="scripted integration only", request_kind="research"))


SUITE = os.environ.get("LIMITUPLAB_EVAL_SMOKE_SUITE")


def test_local_tools_are_shared_not_per_case():
    assert len(LOCAL_TOOLS) == 10
    assert "web_search" not in LOCAL_TOOLS


def test_drift_normalization_only_ignores_post_limit_root_generation_time():
    payload = {"generated_at": "now", "data_as_of": "2026-09-11", "rows": [{"generated_at": "then"}]}
    normalized = baseline_payload("post_limit_path", payload)
    assert normalized == {"data_as_of": "2026-09-11", "rows": [{"generated_at": "then"}]}
    assert baseline_payload("scoring_policy_status", payload) == payload
    assert "generated_at" in payload


def test_failed_readiness_cannot_become_runnable(tmp_path):
    (tmp_path / "report.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="ten successful"):
        build_live_suite(tmp_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.skipif(not SUITE, reason="requires explicit local smoke suite")
@pytest.mark.parametrize("index", range(10))
def test_real_local_tool_through_complete_worker(index, tmp_path, monkeypatch):
    suite_path = Path(SUITE).resolve()
    book = json.loads(suite_path.read_text(encoding="utf-8"))
    entry = [e for e in book["cases"] if e["id"].startswith("LH-B")][index]
    case_path, baseline_path = suite_path.parent / entry["case"], suite_path.parent / entry["baseline"]
    case, baseline = load_case(case_path), load_world(baseline_path)
    record = baseline.recordings[0]

    class ScriptedProvider:
        model = "scripted-integration-not-quality"
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if tools[0]["function"]["name"] == "submit_trace_review":
                dimension = {"verdict": "pass", "rationale": "scripted protocol test", "issue": "", "evidence_ids": []}
                return AIMessage(content="", tool_calls=[{"id": "judge", "name": "submit_trace_review", "args": {
                    key: dimension for key in ("task_completion", "grounding", "boundary_safety")}}])
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{"id": "query", "name": record.tool, "args": record.arguments}])
            return AIMessage(content="", tool_calls=[{"id": "finish", "name": "finish", "args": {
                "status": "empty" if record.observation.state == "empty" else "complete",
                "answer": "本地工具查询已完成；此脚本只验证执行链，不评价回答质量。"}}])

    run = tmp_path / "run"
    run.mkdir()
    provider = ScriptedProvider()
    result = execute_case(case_path, baseline_path, run, provider,
                          live_database=Path(entry["live_database"]), wall_seconds=60, allow_judge=index == 1)
    assert result["verdict"] == "needs_review"
    assert result["primary_cause"] is None
    assert not result["release_eligible"]
    check = json.loads((run / "live-baseline-check.json").read_text(encoding="utf-8"))
    assert check["passed"] and check["checked_recordings"] == [record.id]
    review = json.loads((run / "trace-review.json").read_text(encoding="utf-8"))
    assert review["judge"]["status"] == ("completed" if index == 1 else "disabled")
    assert review["judge"]["calls"] == (1 if index == 1 else 0)
    response = json.loads((run / "response.json").read_text(encoding="utf-8"))
    evidence = next(t["output"] for t in response["tool_results"] if t["name"] == "react_execution")["evidence"]
    assert any(e["tool"] == record.tool for e in evidence.values())
    assert (run / "result.json").exists()
    if index == 0:
        changed = deepcopy(baseline)
        changed.recordings[0].observation.payload = {"corrupted": True}
        with pytest.raises(HistoricalDataDrift):
            SnapshotLiveRegistry(Path(entry["live_database"]), changed, tmp_path / "drift.sqlite")
        # Exercise the actual worker CLI database guard, not just execute_case.
        from app.agent_eval import worker
        cli_run = tmp_path / "cli-run"
        cli_run.mkdir()
        monkeypatch.setattr(worker, "configure_runtime_environment", lambda: None)
        monkeypatch.setattr(worker, "get_llm_provider", ScriptedProvider)
        monkeypatch.setattr(sqlite3, "connect", sqlite3.connect)  # Restore main's guard after test.
        monkeypatch.setattr(sys, "argv", ["worker", "--case", str(case_path), "--world", str(baseline_path),
            "--output-dir", str(cli_run), "--live-database", entry["live_database"], "--allow-llm", "--wall-seconds", "60"])
        assert worker.main() == 2
        assert (cli_run / "result.json").exists()
