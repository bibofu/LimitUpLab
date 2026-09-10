"""CLI and health entrypoints for the seven-stage Chat Eval V2."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from app.repositories import SQLiteFirstBoardRepository
from app.services.system_health import build_agent_system_health


def test_cli_runs_v2_case_and_publishes_latest_report(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    output_root = tmp_path / "reports"
    result = subprocess.run(
        [sys.executable, str(backend / "scripts/run_agent_eval.py"),
         "--case-filter", "CEV2-D001", "--summary-only",
         "--output-root", str(output_root)],
        env={**os.environ, "PYTHONUTF8": "1", "LIMITUPLAB_LLM_ENABLED": "false",
             "LIMITUPLAB_DATABASE_PATH": str(tmp_path / "eval.sqlite")},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["dataset_version"] == "agent-chat-eval-v2"
    assert summary["mode"] == "offline"
    assert (summary["case_count"], summary["passed_cases"], summary["failed_cases"]) == (1, 1, 0)
    latest = json.loads((output_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["status"] == "completed"
    assert latest["results"][0]["case_id"] == "CEV2-D001"


def test_cli_rejects_retired_suite():
    script = Path(__file__).resolve().parents[1] / "scripts/run_agent_eval.py"
    result = subprocess.run(
        [sys.executable, str(script), "--suite", "core"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_cli_judge_configuration_is_never_silently_skipped(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_agent_eval.py"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "LIMITUPLAB_EVAL_JUDGE_MODEL": "",
        "LIMITUPLAB_EVAL_JUDGE_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "OPENAI_API_KEY": "",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--case-filter",
            "CEV2-D001",
            "--judge",
            "--output-root",
            str(tmp_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "configuration_error"
    assert not (tmp_path / "latest.json").exists()


def test_health_runs_only_twelve_offline_smoke_cases(tmp_path):
    with patch("app.services.system_health.load_dev_dataset") as load_dataset, patch(
        "app.services.system_health.run_chat_eval_suite"
    ) as evaluate:
        load_dataset.return_value.cases = [object()] * 120
        evaluate.return_value = {"case_count": 12, "failed_cases": 1}
        report = build_agent_system_health(
            [], first_board_repository=SQLiteFirstBoardRepository(tmp_path / "health.sqlite"),
            run_offline_eval=True,
        )
    evaluate.assert_called_once_with(
        load_dataset.return_value.cases,
        mode="offline",
        trials=1,
        sample_size=12,
        seed="system-health-smoke-v1",
    )
    assert report.offline_eval_total == 12
    assert report.offline_eval_failed == 1
    assert report.offline_eval_passed is False
    assert "Offline Agent eval has failing cases." in report.warnings
