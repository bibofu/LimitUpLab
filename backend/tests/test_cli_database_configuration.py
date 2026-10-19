"""CLI entry points honor local DB selection without leaking connection secrets."""

import importlib
import os
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize("module_name", [
    "dev_check", "update_daily_data", "sync_first_board_history",
    "import_limit_up_from_akshare", "run_factor_signal_diagnostic",
    "audit_prediction_times", "research_limit_up_paths", "postgres_admin",
    "migrate_sqlite_to_postgres", "run_recommendation_refresh_loop", "import_sample_data",
    "run_daily_close_loop", "run_quality_worker", "run_quality_judge_worker",
])
def test_cli_loads_selected_env_before_dispatch(module_name, tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    url = "postgresql://local:do-not-print-this-secret@127.0.0.1:5432/local"
    env_file.write_text(f"LIMITUPLAB_DATABASE_URL={url}\n", encoding="utf-8")
    monkeypatch.setenv("LIMITUPLAB_ENV_FILE", str(env_file))
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", [module_name, "--help"])
    # Help exercises the real entry point and .env loader without querying or
    # changing any database or collecting market data.
    module = importlib.import_module("scripts." + module_name)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 0
    assert os.environ["LIMITUPLAB_DATABASE_URL"] == url
    output = capsys.readouterr()
    assert "do-not-print-this-secret" not in output.out + output.err


def test_test_collection_uses_isolated_storage():
    # PG integration fixtures explicitly opt into LIMITUPLAB_TEST_DATABASE_URL;
    # ordinary tests always have a scratch SQLite and no project .env loaded.
    assert os.environ["LIMITUPLAB_DATABASE_URL"] == ""
    assert not Path(os.environ["LIMITUPLAB_ENV_FILE"]).exists()
    assert "pytest-runtime-" in Path(os.environ["LIMITUPLAB_DATABASE_PATH"]).parent.name
