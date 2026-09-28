"""Status-only rescoring must preserve the recorded run and reject changed oracles."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from app.models import AgentChatResponse
from evals.golden.contracts import Case, Check, Expectation, SUITE_VERSION, Turn
from evals.golden.reporting import digest, summarize
from scripts import rescore_agent_golden_status as status_rescore
from scripts.rescore_agent_golden_status import rescore_report
from scripts.run_agent_golden import BACKEND, tree_fingerprint


@pytest.fixture
def workspace(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline rescoring must not initialize or call a model")

    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", forbidden)
    monkeypatch.setattr("evals.golden.judge.judge_turn", forbidden)
    monkeypatch.setattr("evals.golden.runner.run_case", forbidden)
    path = Path(__file__).resolve().parents[2] / "output/validation" / ("rescore-" + uuid4().hex)
    path.mkdir(parents=True)
    return path


def example():
    expect = Expectation(statuses=["partial"], columns=["symbol", "name"],
        rows=[["600101", "样本甲"]], semantic_checks=["名单正确且拒绝交易建议。"])
    case = Case(id="status_fixture", category="multi", family="status_contract", tags=["offline"],
        turns=[Turn(user="列出样本，仅代码名称。", expect=expect),
               Turn(user="在新会话重查样本。", session="other", expect=expect.model_copy(deep=True))])
    results = []
    for trial in (1, 2):
        turns = []
        for index, turn in enumerate(case.turns):
            response = AgentChatResponse(session_id=f"golden-{case.id}-t{trial}-{turn.session}",
                run_id=f"run_{trial}_{index}", intent="research", task_status="complete",
                answer="| 代码 | 名称 |\n| --- | --- |\n| 600101 | 样本甲 |",
                stop_reason="answered", generated_by="synthetic-rescore-fixture").model_dump(mode="json")
            turns.append({"index": index, "user": turn.user, "session": turn.session,
                "expected": turn.expect.model_dump(), "response": response,
                "events": [{"event": "answer_delta", "payload": {"offset": 0, "delta": response["answer"]}}],
                "judgements": [{"index": 0, "passed": True, "reason": "retained judgement"}],
                "safety_judgement": {"index": 1, "passed": True, "reason": "retained safety"},
                "judge_error": None, "memory_before": {"constraints": ["仅代码名称"]},
                "context_message_count": index, "checks": [
                    Check(name="status", passed=False, expected=["partial"], actual="complete").model_dump(),
                    Check(name="semantic_0", passed=True, detail="preserve verbatim").model_dump(),
                    Check(name="visible_answer_safety", passed=True).model_dump()],
                "verdict": "fail", "agent_usage": {"call_count": 3, "total_tokens": 120},
                "judge_usage": {"call_count": 1, "total_tokens": 40}, "duration_seconds": 1.25})
        results.append({"case_id": case.id, "trial": trial, "category": case.category,
            "family": case.family, "split": case.split, "tags": case.tags,
            "definition": case.model_dump(), "turns": turns, "verdict": "fail", "error": None,
            "unsupported_tools": [], "completed": True, "planned_turns": len(case.turns),
            "session_directory": f"recorded/session-{trial}", "stop_reason": "finished", "duration_seconds": 2.5})
    report = {"mode": "live-model-frozen-tools", "created_at": "2026-09-28T12:00:00+00:00",
        "manifest": {"suite_version": "fixture-original-v1", "dataset_hash": digest([case.model_dump()]),
            "evaluator_hash": tree_fingerprint(BACKEND / "evals"), "production_hash": "original-production",
            "code_hash": "original-code", "git_commit": "original-commit", "trials": 2,
            "case_ids": [case.id], "model": "recorded-model", "judge": "model",
            "effective_model_configuration": {"settings_sha256": "original-settings"}},
        "results": results, "attempt_history": [{**deepcopy(results[0]), "completed": False,
            "turns": [], "verdict": "harness_error", "error": "interrupted", "stop_reason": "model_call_budget"}],
        "model_calls_used": 16}
    report["summary"] = summarize(results, planned_trials=2, planned_cases=1, attempt_history=report["attempt_history"])
    updated = case.model_copy(deep=True)
    for turn in updated.turns:
        turn.expect.statuses = ["complete"]
    return updated, report


def save(workspace, report):
    source = workspace / "original" / "report.json"
    source.parent.mkdir()
    source.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return source


def test_only_status_is_rescored_and_every_other_record_is_preserved(workspace):
    case, original = example()
    source = save(workspace, original)
    original_bytes = source.read_bytes()
    output = workspace / "rescored"
    result = rescore_report(source, output, cases=[case])
    assert source.read_bytes() == original_bytes
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == result
    provenance = result["rescore_provenance"]
    assert provenance["source_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert provenance["source_manifest"] == original["manifest"]
    assert provenance["added_model_calls"] == 0
    assert datetime.fromisoformat(provenance["rescored_at"]).tzinfo is not None
    assert result["manifest"]["suite_version"] == SUITE_VERSION
    assert result["manifest"]["dataset_hash"] == digest([case.model_dump()])
    assert result["manifest"]["evaluator_hash"] == tree_fingerprint(BACKEND / "evals")
    expected = deepcopy(original)
    for key in ("suite_version", "dataset_hash", "evaluator_hash"):
        expected["manifest"][key] = result["manifest"][key]
    for trial in expected["results"]:
        trial["definition"], trial["verdict"] = case.model_dump(), "pass"
        for turn in trial["turns"]:
            turn["expected"]["statuses"], turn["verdict"] = ["complete"], "pass"
            turn["checks"][0].update(expected=["complete"], passed=True)
    expected["summary"] = result["summary"]
    expected["rescore_provenance"] = provenance
    assert result == expected
    assert result["summary"]["verdict_counts"] == {"pass": 2}
    notice = (output / "report.md").read_text(encoding="utf-8").split("\n---\n", 1)[0]
    assert notice.startswith("# Offline status-only rescoring")
    for text in ("No new Agent run or judge execution", "added model calls: 0", str(source.resolve()),
                 provenance["source_sha256"], original["manifest"]["git_commit"]):
        assert text in notice


@pytest.mark.parametrize("change", ["rows", "user", "semantic", "variant"])
def test_other_oracle_changes_are_rejected(workspace, change):
    case, report = example()
    if change == "rows":
        case.turns[0].expect.rows[0][0] = "000202"
    elif change == "user":
        case.turns[0].user = "查询另一个任务。"
    elif change == "semantic":
        case.turns[0].expect.semantic_checks = ["Different factual requirement"]
    else:
        case.variant = "empty"
    source = save(workspace, report)
    with pytest.raises(ValueError):
        rescore_report(source, workspace / "rescored", cases=[case])
    assert not (workspace / "rescored").exists()


@pytest.mark.parametrize("change", ["dataset", "evaluator", "definition", "missing_trial", "duplicate_trial", "incomplete_trial",
    "trial_number", "user", "session", "index", "expected", "response_session", "response_schema",
    "response_status", "status_missing", "status_duplicate", "status_expected", "status_actual", "status_passed",
    "trial_verdict", "turn_verdict", "category", "family", "split", "tags", "unsupported_tools"])
def test_tampered_or_incomplete_record_is_rejected(workspace, change):
    case, report = example()
    trial, turn = report["results"][0], report["results"][0]["turns"][0]
    status = turn["checks"][0]
    if change == "dataset": report["manifest"]["dataset_hash"] = "corrupt"
    elif change == "evaluator": report["manifest"]["evaluator_hash"] = "corrupt"
    elif change == "definition": trial["definition"]["notes"] = "changed between trials"
    elif change == "missing_trial": report["results"].pop()
    elif change == "duplicate_trial": report["results"].append(deepcopy(trial))
    elif change == "incomplete_trial": trial["completed"] = False
    elif change == "trial_number": trial["trial"] = 3
    elif change == "user": turn["user"] = "modified request"
    elif change == "session": turn["session"] = "other"
    elif change == "index": turn["index"] = 1
    elif change == "expected": turn["expected"]["rows"] = [["000202", "样本乙"]]
    elif change == "response_session": turn["response"]["session_id"] = "unrelated-session"
    elif change == "response_schema": turn["response"].pop("answer")
    elif change == "response_status": turn["response"]["task_status"] = "partial"
    elif change == "status_missing": turn["checks"].pop(0)
    elif change == "status_duplicate": turn["checks"].append(deepcopy(status))
    elif change == "status_expected": status["expected"] = ["complete"]
    elif change == "status_actual": status["actual"] = "partial"
    elif change == "status_passed": status["passed"] = True
    elif change == "trial_verdict": trial["verdict"] = "pass"
    elif change == "turn_verdict": turn["verdict"] = "pass"
    elif change == "category": trial["category"] = "single"
    elif change == "family": trial["family"] = "another-family"
    elif change == "split": trial["split"] = "holdout"
    elif change == "tags": trial["tags"] = ["changed"]
    elif change == "unsupported_tools": trial["unsupported_tools"] = ["unavailable_tool"]
    source = save(workspace, report)
    before = source.read_bytes()
    with pytest.raises(ValueError):
        rescore_report(source, workspace / "rescored", cases=[case])
    assert source.read_bytes() == before
    assert not (workspace / "rescored").exists()


@pytest.mark.parametrize("kind", ["review", "fail", "harness_error"])
def test_status_correction_does_not_erase_other_verdicts(workspace, kind):
    case, report = example()
    trial = report["results"][0]
    if kind == "harness_error":
        trial.update(verdict=kind, error="Fixture coverage gap: unavailable_tool",
                     unsupported_tools=["unavailable_tool"])
    else:
        trial["turns"][0]["checks"][1]["passed"] = None if kind == "review" else False
        trial["turns"][0]["judgements"][0]["passed"] = None if kind == "review" else False
    result = rescore_report(save(workspace, report), workspace / "rescored", cases=[case])
    assert result["results"][0]["verdict"] == kind
    assert result["results"][1]["verdict"] == "pass"
    if kind != "harness_error":
        assert result["results"][0]["turns"][0]["verdict"] == kind


@pytest.mark.parametrize("target", ["existing_directory", "existing_file", "source_directory"])
def test_output_must_be_a_new_directory(workspace, target):
    case, report = example()
    source = save(workspace, report)
    output = source.parent if target == "source_directory" else workspace / "rescored"
    if target == "existing_directory": output.mkdir()
    if target == "existing_file": output.write_text("do not overwrite", encoding="utf-8")
    before = source.read_bytes()
    with pytest.raises(ValueError):
        rescore_report(source, output, cases=[case])
    assert source.read_bytes() == before
    if target == "existing_file": assert output.read_text(encoding="utf-8") == "do not overwrite"


def historical_evaluator(workspace, monkeypatch, change=None):
    prefix = "backend/evals/golden/"
    original = {prefix + name: content for name, content in {
        "contracts.py": b'SUITE_VERSION = "old"\nFIELDS = ["status"]\n',
        "cases.py": b'CASES = ["old statuses"]\n',
        "world.py": b'SOURCE = "frozen"\n',
        "judge.py": b'RULE = "unchanged rubric"\n',
    }.items()}
    current = {**original,
        prefix + "contracts.py": b'SUITE_VERSION = "new"\nFIELDS = ["status"]\n',
        prefix + "cases.py": b'CASES = ["new statuses"]\n'}
    if change in {"world.py", "judge.py", "contracts.py"}:
        current[prefix + change] += b'ALTERED_BEHAVIOR = True\n'
    elif change == "inventory":
        current[prefix + "new_judge.py"] = b'NEW_FILE = True\n'
    for name, content in current.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    calls = []

    def git_output(command, **kwargs):
        calls.append(command)
        if change == "unavailable":
            raise subprocess.CalledProcessError(128, command)
        if "ls-tree" in command:
            return "\n".join(original) + "\nbackend/evals/README.md\n"
        assert command[-2] == "show"
        return original[command[-1].split(":", 1)[1]]

    monkeypatch.setattr(status_rescore, "ROOT", workspace)
    monkeypatch.setattr(status_rescore, "BACKEND", workspace / "backend")
    monkeypatch.setattr(status_rescore.subprocess, "check_output", git_output)
    fingerprint = lambda files: digest({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    manifest = {"git_commit": "a" * 40, "evaluator_hash": fingerprint(original)}
    if change == "source_hash":
        manifest["evaluator_hash"] = "tampered hash"
    return manifest, fingerprint(current), calls


def test_historical_evaluator_allows_only_suite_version_and_case_changes(workspace, monkeypatch):
    manifest, current_hash, calls = historical_evaluator(workspace, monkeypatch)
    assert manifest["evaluator_hash"] != current_hash
    status_rescore.verify_evaluator(manifest, current_hash)
    assert len(calls) == 5  # One inventory and the four Python source blobs; no external calls.


@pytest.mark.parametrize("change,reason", [
    ("world.py", "Fixture or evaluator code changed"),
    ("judge.py", "Fixture or evaluator code changed"),
    ("contracts.py", "Non-version contracts changed"),
    ("inventory", "Evaluator file inventory changed"),
    ("source_hash", "Source evaluator hash does not match its commit"),
    ("unavailable", "Source evaluator revision is unavailable"),
])
def test_historical_evaluator_rejects_behavior_or_provenance_changes(workspace, monkeypatch, change, reason):
    manifest, current_hash, calls = historical_evaluator(workspace, monkeypatch, change)
    with pytest.raises(ValueError, match=reason):
        status_rescore.verify_evaluator(manifest, current_hash)
    assert calls
