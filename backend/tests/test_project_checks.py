"""Ensure the acceptance command cannot hide a failed gate."""
import importlib.util
import os
from pathlib import Path
import sys

script = Path(__file__).resolve().parents[2] / "scripts/check_project.py"
spec = importlib.util.spec_from_file_location("project_checks", script)
checks = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checks
spec.loader.exec_module(checks)


# Regression scenario: failure is reported and later independent gate still runs.
def test_failure_is_reported_and_later_independent_gate_still_runs(tmp_path):
    commands = [
        checks.Check("bad", [sys.executable, "-c", "raise SystemExit(7)"], tmp_path),
        checks.Check("good", [sys.executable, "-c", "print('still checked')"], tmp_path),
    ]
    report = checks.run_checks(commands, tmp_path, dict(os.environ), 10)
    assert not report["passed"]
    assert [item["exit_code"] for item in report["checks"]] == [7, 0]
    assert "still checked" in (tmp_path / "good.log").read_text()
    assert (tmp_path / "summary.json").is_file()


# Regression scenario: missing executable is a failed gate.
def test_missing_executable_is_a_failed_gate(tmp_path):
    command = checks.Check("missing", [str(tmp_path / "no-such-executable")], tmp_path)
    report = checks.run_checks([command], tmp_path, dict(os.environ), 10)
    assert not report["passed"]
    assert report["checks"][0]["error"]


# Regression scenario: backend gates use offline eval and report local paths.
def test_backend_gates_use_offline_eval_and_report_local_paths(tmp_path):
    plan = checks.build_checks("backend", tmp_path)
    assert [item.name for item in plan] == ["pytest", "eval-core", "eval-product"]
    for item in plan[1:]:
        assert item.command[item.command.index("--mode") + 1] == "offline"
        assert str(tmp_path) in item.command[-1]
