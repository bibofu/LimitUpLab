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


# Regression scenario: backend validation remains available while Agent eval is retired.
def test_backend_gate_runs_pytest_only(tmp_path):
    plan = checks.build_checks("backend", tmp_path)
    assert [item.name for item in plan] == ["pytest"]


def test_acceptance_runner_does_not_inherit_active_database(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://local:secret@localhost/live")
    monkeypatch.setenv("LIMITUPLAB_ENV_FILE", str(tmp_path / "live.env"))
    monkeypatch.setattr(sys, "argv", ["check_project.py", "--scope", "backend"])
    captured = {}

    def capture_environment(plan, output, env, timeout):
        captured.update(env)
        return {"passed": True}

    monkeypatch.setattr(checks, "run_checks", capture_environment)
    assert checks.main() == 0
    assert captured["LIMITUPLAB_DATABASE_URL"] == ""
    assert not Path(captured["LIMITUPLAB_ENV_FILE"]).exists()
    assert "LIMITUPLAB_DATABASE_PATH" not in captured
    assert captured["LIMITUPLAB_TEST_DATABASE_URL"] == os.environ["LIMITUPLAB_TEST_DATABASE_URL"]
