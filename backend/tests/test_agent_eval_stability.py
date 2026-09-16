import json
from pathlib import Path

import pytest

from app.agent_eval.core_batch import write_json
from app.agent_eval.full_answer_judge import JUDGE_SYSTEM as FULL_ANSWER_SYSTEM
from app.agent_eval.recorder import digest
from app.agent_eval.semantic_acceptance import JUDGE_SYSTEM as SEMANTIC_SYSTEM
from app.agent_eval.stability import (
    build_stability_panel, verify_formal_manifest, verify_stability_manifest,
)


def _formal(tmp_path: Path, index: int, *, full_answer="pass"):
    suite_dir, acceptance_dir = tmp_path / "suite", tmp_path / "acceptance"
    suite_dir.mkdir(exist_ok=True)
    acceptance_dir.mkdir(exist_ok=True)
    suite = {"suite_id": "local30-current", "cases": [{"id": "OFF-001"}]}
    acceptance = {"technical_acceptance": True, "index": 1}
    if not (suite_dir / "suite.json").exists():
        write_json(suite_dir / "suite.json", suite)
        write_json(acceptance_dir / "acceptance.json", acceptance)
    run_root = tmp_path / f"run-{index}"
    case_dir = run_root / "OFF-001"
    case_dir.mkdir(parents=True)
    write_json(case_dir / "request.json", {"session_id": f"session-{index}", "message_id": f"message-{index}"})
    write_json(case_dir / "response.json", {"tool_results": [
        {"name": "react_decision"}, {"name": "market_summary"}, {"name": "react_execution"},
    ]})
    batch = {"cases": [{"id": "OFF-001"}], "total_tokens": 100}
    write_json(run_root / "batch-results.json", batch)
    formal = tmp_path / f"formal-{index}"
    formal.mkdir()
    report = {
        "schema_version": "formal-agent-baseline-v3", "suite_id": "local30-current",
        "model": "model", "case_count": 1, "run_root": str(run_root.resolve()),
        "core_contract_pass_rate": 1.0, "terminal_accuracy": 1.0,
        "full_answer_pass_rate": 1.0 if full_answer == "pass" else 0.0,
        "infrastructure_success_rate": 1.0, "agent_model_calls": 2, "agent_tokens": 100,
        "judge_model_calls": 1, "judge_tokens": 10, "latency_seconds": {"p50": 1.0, "p95": 1.0},
        "cases": [{"case_id": "OFF-001", "core_contract_verdict": "pass",
                   "terminal_verdict": "pass", "full_answer_verdict": full_answer,
                   "actual_terminal": "complete", "failure_cause": None,
                   "agent_model_calls": 2, "agent_tokens": 100, "elapsed_seconds": 1.0}],
    }
    write_json(formal / "report.json", report)
    (formal / "README.md").write_text("formal\n", encoding="utf-8")
    manifest = {
        "schema_version": "formal-agent-run-manifest-v1", "suite_id": "local30-current",
        "model": "model", "case_count": 1, "runtime_versions": ["runtime"],
        "judge_prompt_digests": {"semantic": digest(SEMANTIC_SYSTEM),
                                 "full_answer": digest(FULL_ANSWER_SYSTEM)},
        "inputs": {
            "suite": {"path": str(suite_dir.resolve()), "digest": digest(suite)},
            "run": {"path": str(run_root.resolve()), "batch_digest": digest(batch)},
            "acceptances": [{"path": str((acceptance_dir / 'acceptance.json').resolve()),
                             "digest": digest(acceptance)}],
        },
        "artifacts": {"report.json": digest(report), "README.md": digest("formal\n")},
    }
    write_json(formal / "manifest.json", manifest)
    return formal


def test_formal_manifest_verifies_artifacts_and_inputs(tmp_path):
    formal = _formal(tmp_path, 1)
    assert verify_formal_manifest(formal / "manifest.json")["valid"]
    (formal / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact"):
        verify_formal_manifest(formal / "manifest.json")


def test_stability_panel_requires_three_independent_passing_runs(tmp_path):
    formals = [_formal(tmp_path, index) for index in (1, 2, 3)]
    destination = tmp_path / "panel"
    report = build_stability_panel(formals, destination)
    assert report["stability_eligible"] is True
    assert report["stable_case_count"] == 1
    assert report["unique_request_identities"] == 3
    assert report["route_volatile_case_count"] == 0
    assert verify_stability_manifest(destination / "manifest.json")["valid"]


def test_stability_panel_exposes_answer_instability(tmp_path):
    formals = [_formal(tmp_path, 1), _formal(tmp_path, 2), _formal(tmp_path, 3, full_answer="fail")]
    report = build_stability_panel(formals, tmp_path / "panel")
    assert report["stability_eligible"] is False
    assert report["cases"][0]["pass_counts"]["full_answer"] == 2
