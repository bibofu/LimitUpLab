"""CLI and health must use Golden and preserve genuine evaluation failures."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from app.repositories import SQLiteFirstBoardRepository
from app.services.system_health import build_agent_system_health


def test_cli_defaults_to_golden_and_keeps_failure_exit_code(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    report_path = tmp_path / "failures.json"
    result = subprocess.run(
        [sys.executable, str(backend / "scripts/run_agent_eval.py"),
         "--case-filter", "G050", "--summary-only", "--failure-output", str(report_path)],
        env={**os.environ, "PYTHONUTF8": "1", "LIMITUPLAB_LLM_ENABLED": "false",
             "LIMITUPLAB_DATABASE_PATH": str(tmp_path / "eval.sqlite")},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary["suite"] == "golden"
    assert (summary["total"], summary["passed"], summary["failed"]) == (1, 0, 1)
    assert "query_contract" not in summary
    failure = json.loads(report_path.read_text(encoding="utf-8"))["results"][0]
    assert failure["case_id"] == "G050"
    assert failure["layers"]["answer"]["failures"]


def test_cli_rejects_retired_suite():
    script = Path(__file__).resolve().parents[1] / "scripts/run_agent_eval.py"
    result = subprocess.run(
        [sys.executable, str(script), "--suite", "core"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_health_reports_golden_failures_without_adding_other_suites(tmp_path):
    with patch("app.services.system_health.run_default_golden_eval") as evaluate:
        evaluate.return_value = SimpleNamespace(total=2, failed=1, ok=False)
        report = build_agent_system_health(
            [], first_board_repository=SQLiteFirstBoardRepository(tmp_path / "health.sqlite"),
            run_offline_eval=True,
        )
    evaluate.assert_called_once_with()
    assert report.offline_eval_total == 2
    assert report.offline_eval_failed == 1
    assert report.offline_eval_passed is False
    assert "Offline Agent eval has failing cases." in report.warnings
