import json

import pytest

from app.agent_eval.acceptance_run import run_selected, select_entries, summarize_runs
from app.agent_eval.recorder import digest
from app.agent_eval.basic70 import build_basic70


def test_selection_verifies_assets_and_rejects_missing_duplicate_ids(tmp_path):
    output = tmp_path / "assets"
    build_basic70(output)
    suite = output / "suite.json"
    assert len(select_entries(suite, ["OFF-B001"])) == 1
    for ids in (["missing"], ["OFF-B001", "OFF-B001"]):
        with pytest.raises(ValueError):
            select_entries(suite, ids)
    path = output / "OFF-B001" / "case.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    case["conversation"][0]["content"] += "tampered"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        select_entries(suite, ["OFF-B001"])


def test_existing_runs_and_unbounded_execution_are_rejected(tmp_path):
    assets = tmp_path / "assets"
    build_basic70(assets)
    output = tmp_path / "runs"
    (output / "OFF-B001").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        run_selected(assets / "suite.json", output, ["OFF-B001"])
    with pytest.raises(ValueError, match="bounded"):
        run_selected(assets / "suite.json", output, ["OFF-B002"], workers=3)


def test_report_preserves_original_failure_and_checks_supplement_binding(tmp_path):
    runs, extra = tmp_path / "runs", tmp_path / "extra"
    folder, supplement = runs / "OFF-B001", extra / "OFF-B001"
    folder.mkdir(parents=True)
    supplement.mkdir(parents=True)
    files = {"summary.json": {"verdict": "unscorable", "primary_cause": "fixture_failure", "total_tokens": 10, "model_calls": 2},
             "trace-review.json": {"judge": {"status": "completed"}, "dimensions": {}},
             "response.json": {"task_status": "partial"}, "case.json": {}, "result.json": {},
             "usage.json": {}, "manifest.json": {}, "frozen-attempts.json": [{"outcome": "rejected"}]}
    for name, value in files.items():
        (folder / name).write_text(json.dumps(value), encoding="utf-8")
    binding = {"case_digest": digest({}), "response_digest": digest(files["response.json"]), "total_tokens": 3, "calls": 1}
    (supplement / "binding.json").write_text(json.dumps(binding), encoding="utf-8")
    (supplement / "review.json").write_text(json.dumps(files["trace-review.json"]), encoding="utf-8")
    report = summarize_runs(runs, extra, tmp_path / "report.json")
    assert report["initial_worker_verdicts"] == {"unscorable": 1}
    assert report["initial_recorded_tokens"] == 10 and report["supplement_recorded_tokens"] == 3
    assert report["new_active_golden"] == 0
    binding["response_digest"] = "tampered"
    (supplement / "binding.json").write_text(json.dumps(binding), encoding="utf-8")
    with pytest.raises(ValueError, match="supplement"):
        summarize_runs(runs, extra, tmp_path / "invalid.json")
    assert not (tmp_path / "invalid.json").exists()
