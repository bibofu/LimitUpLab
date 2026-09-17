import json
from pathlib import Path

import pytest

from app.agent_eval.golden_run import classify, plan_suite, run_golden
from app.agent_eval.admission import promote_approved
from app.agent_eval.core_batch import write_json
from test_agent_eval_admission import source_suite
from test_agent_eval_checks import artifact, no_external_calls


def active(tmp_path):
    source = source_suite(tmp_path)
    dest = tmp_path / "active"
    promote_approved(source, dest, ["OFF-B002"], approval_text="test approval")
    return dest / "suite.json"


def review(verdict="pass", status="completed"):
    return {"judge": {"status": status}, "dimensions": {k: {"verdict": verdict}
        for k in ("task_completion", "grounding", "boundary_safety")}}


@pytest.mark.parametrize("summary,judge,expected", [
    ({"verdict": "needs_review"}, review(), "pass"),
    ({"verdict": "needs_review"}, review("fail"), "fail"),
    ({"verdict": "needs_review"}, review("needs_review"), "needs_review"),
    ({"verdict": "needs_review"}, None, "needs_review"),
    ({"verdict": "needs_review", "process_diagnostic": "fail"}, None, "fail"),
    ({"verdict": "needs_review"}, review(status="disabled"), "needs_review"),
    ({"verdict": "needs_review"}, review(status="input_budget_exceeded"), "unscorable"),
    ({"verdict": "needs_review"}, review(status="judge_error"), "unscorable"),
    ({"verdict": "unscorable", "primary_cause": "fixture_failure"}, review(), "unscorable"),
    ({"verdict": "fail", "primary_cause": "agent_failure"}, review(), "fail"),
    ({"verdict": "needs_review", "primary_cause": "evaluator_failure"}, review(), "unscorable"),
    ({"verdict": "needs_review"}, {"judge": {"status": "skipped_hard_failure"},
        "deterministic": {"process": {"findings": [{"verdict": "fail"}]}}}, "fail"),
])
def test_diagnostic_classification(summary, judge, expected):
    assert classify(summary, judge)[0] == expected


def test_dry_run_never_executes_or_overwrites(tmp_path):
    suite = active(tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError("unexpected model run")
    result = run_golden(suite, tmp_path / "dry", dry_run=True, executor=forbidden)
    assert result["counts"] == {"not_run": 2} and result["diagnostic_pass_rate"] is None
    with pytest.raises(FileExistsError):
        run_golden(suite, tmp_path / "dry", dry_run=True)
    with pytest.raises(ValueError, match="allow-llm"):
        run_golden(suite, tmp_path / "unauthorized")
    with pytest.raises(ValueError, match="selection"):
        plan_suite(suite, ["missing"])


def test_batch_preserves_failure_and_reports_coverage(tmp_path):
    suite = active(tmp_path)
    def executor(case, baseline, folder, **kwargs):
        if folder.name == "OFF-B002": raise TimeoutError("scripted worker failure")
        folder.mkdir()
        write_json(folder / "trace-review.json", review())
        return {"verdict": "needs_review", "total_tokens": 10}
    result = run_golden(suite, tmp_path / "run", allow_llm=True, executor=executor)
    assert result["counts"] == {"pass": 1, "unscorable": 1}
    assert result["scoring_coverage"] == 0.5 and result["diagnostic_pass_rate"] == 1
    assert not result["release_eligible"] and not result["token_usage_complete"]
    assert (tmp_path / "run/OFF-B002-result.json").exists()
    assert (tmp_path / "run/REPORT.md").exists()


def test_golden64_routing_when_local_assets_available(tmp_path):
    suite = Path(__file__).resolve().parents[2] / "output/agent-eval/golden/basic64-current-v1/suite.json"
    database = suite.parents[2] / "basic70-live-readiness-003/source-snapshot.sqlite"
    if not suite.exists() or not database.exists(): pytest.skip("local Golden64 assets not distributed")
    routed = []
    def scripted(case, baseline, folder, **kwargs):
        routed.append((folder.name, kwargs["live_database"]))
        folder.mkdir()
        write_json(folder / "trace-review.json", review())
        return {"verdict": "needs_review", "total_tokens": 0}
    result = run_golden(suite, tmp_path / "all64", live_database=database,
                         allow_llm=True, executor=scripted)
    assert result["case_count"] == 64 and result["counts"] == {"pass": 64}
    assert sum(db is not None for _, db in routed) == 19
    assert result["execution_kind"] == "injected_test_executor"


def test_legacy_worker_uses_shared_judge_without_changing_extraction(artifact, tmp_path):
    from langchain_core.messages import AIMessage
    from app.agent_eval.candidates import save_summary_candidate
    from app.agent_eval.worker import execute_case
    from test_agent_eval_worker import ScriptedProvider
    class Provider(ScriptedProvider):
        judge_calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_trace_review":
                self.judge_calls += 1
                return AIMessage(content="", tool_calls=[{"id": "judge", "name": "submit_trace_review", "args": {
                    k: {"verdict": "pass", "rationale": "scripted diagnostic", "issue": "", "evidence_ids": []}
                    for k in ("task_completion", "grounding", "boundary_safety")}}])
            return super().generate_messages(messages, tools, **kwargs)
    assets, folder = tmp_path / "legacy", tmp_path / "worker"
    save_summary_candidate(artifact, assets)
    folder.mkdir()
    provider = Provider()
    result = execute_case(assets / "case.json", assets / "world.json", folder, provider, allow_judge=True)
    assert provider.judge_calls == 1 and (folder / "extraction.json").exists()
    assert result["trace_review"]["judge"]["status"] == "completed"
    assert classify(result, json.loads((folder / "trace-review.json").read_text(encoding="utf-8")))[0] == "pass"
