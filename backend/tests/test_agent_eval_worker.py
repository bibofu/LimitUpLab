"""End-to-end harness tests use scripted models; real invocations remain opt-in."""

import json
import subprocess
from time import perf_counter

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from app.agent_eval.candidates import save_summary_candidate
from app.agent_eval.extractor import extract_answer
from app.agent_eval.models import BudgetSpec
from app.agent_eval.runner import run_offline
from app.agent_eval.worker import GuardedProvider, execute_case
from test_agent_eval_checks import artifact, no_external_calls


ANSWER = "2026-09-11 的涨停家数见本轮查询。"


class ScriptedProvider:
    model = "scripted-not-real"

    def generate_messages(self, messages, tools, **kwargs):
        if tools[0]["function"]["name"] == "submit_extraction":
            payload = json.loads(messages[-1].content)
            assert set(payload) == {"answer"}  # Never leak case/world/expected truth to extractor.
            return AIMessage(content="", tool_calls=[{"id": "x", "name": "submit_extraction", "args": {
                "items": [{"quote": payload["answer"], "occurrence": 0, "numeric": False,
                           "relational": True, "claim": None}]}}])
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content=ANSWER)
        return AIMessage(content="", tool_calls=[{"id": "q", "name": "market_summary", "args": {}}])


def test_scripted_full_worker_preserves_artifacts_and_never_promotes_model_inventory(artifact, tmp_path):
    assets, output = tmp_path / "assets", tmp_path / "run"
    save_summary_candidate(artifact, assets)
    output.mkdir()
    report = execute_case(assets / "case.json", assets / "world.json", output, ScriptedProvider())
    assert report["verdict"] == "needs_review" and not report["release_eligible"]
    assert report["model_calls"] == 3 and report["total_tokens"] is None
    for name in ["manifest", "request", "case", "world", "source", "response", "extraction", "trajectory", "facts", "result", "usage", "summary"]:
        assert (output / (name + ".json")).is_file()
    extraction = json.loads((output / "extraction.json").read_text(encoding="utf-8"))
    assert not extraction["inventory_complete"] and extraction["inventory_reviewers"] == []
    facts = json.loads((output / "facts.json").read_text(encoding="utf-8"))
    assert facts["claim_coverage"] is None and facts["extraction_status"] == "unreviewed"
    assert facts["scope"] == "provisional_summary_numeric_claims"
    assert report["trajectory"] == "pass"
    output2 = tmp_path / "run2"
    output2.mkdir()
    execute_case(assets / "case.json", assets / "world.json", output2, ScriptedProvider())
    assert (output / "request.json").read_bytes() != (output2 / "request.json").read_bytes()


def test_extractor_failure_keeps_agent_response_and_raw_output(artifact, tmp_path):
    class Broken(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_extraction":
                return AIMessage(content="not a structured extraction")
            return super().generate_messages(messages, tools, **kwargs)
    assets, output = tmp_path / "assets", tmp_path / "run"
    save_summary_candidate(artifact, assets)
    output.mkdir()
    report = execute_case(assets / "case.json", assets / "world.json", output, Broken())
    assert report["primary_cause"] == "evaluator_failure"
    assert (output / "response.json").is_file() and (output / "extraction-error.json").is_file()
    assert (output / "call-03-response.json").is_file()


def test_extractor_rejects_fabricated_quotes():
    class Model:
        def generate_messages(self, *args, **kwargs):
            return AIMessage(content="", tool_calls=[{"id": "x", "name": "submit_extraction", "args": {
                "items": [{"quote": "not in answer", "occurrence": 0, "numeric": True,
                           "relational": True, "claim": None}]}}])
    with pytest.raises(ValueError, match="quote"):
        extract_answer(Model(), ANSWER)


def test_business_contract_does_not_misuse_latest_summary_truth(artifact, tmp_path):
    from app.agent_eval.loader import load_case
    assets, output = tmp_path / "assets", tmp_path / "run"
    save_summary_candidate(artifact, assets)
    case = load_case(assets / "case.json")
    case.assertions[-1].target = "answer.business_contract"
    case.assertions[-1].expected = {"trade_date":"2026-09-10","limit_up_count":35}
    (assets / "case.json").write_text(case.model_dump_json(), encoding="utf-8")
    class Provider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_event_list":
                return AIMessage(content="", tool_calls=[{"id":"extract","name":"submit_event_list",
                    "args":{"trade_date":"2026-09-10","members":[],
                            "additional_claim_line_ids":[0],"ambiguous":False}}])
            return super().generate_messages(messages, tools, **kwargs)
    output.mkdir()
    result = execute_case(assets / "case.json", assets / "world.json", output, Provider())
    assert result["fact_diagnostic"] == "needs_review"
    facts = json.loads((output / "facts.json").read_text(encoding="utf-8"))
    assert not any(f["verdict"] == "fail" for f in facts["findings"])


def test_call_budget_is_enforced_before_provider_call(tmp_path):
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=1, max_input_tokens=100,
                        max_output_tokens=100, max_estimated_cost_usd=None, max_wall_time_seconds=10)
    provider = GuardedProvider(ScriptedProvider(), tmp_path, perf_counter() + 10, budget)
    tools = [{"function": {"name": "market_summary"}}]
    provider.generate_messages([], tools)
    with pytest.raises(TimeoutError):
        provider.generate_messages([], tools)
    assert provider.calls == 1 and not (tmp_path / "call-02-request.json").exists()


def test_supervisor_kills_only_its_timed_out_worker_and_retains_directory(tmp_path, monkeypatch):
    case, world = tmp_path / "case.json", tmp_path / "world.json"
    case.write_text("{}")
    world.write_text("{}")
    class Process:
        killed = False
        def __init__(self, command, **kwargs):
            assert "app.agent_eval.worker" in command and "--allow-llm" in command
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("test-worker", timeout)
            return 1
        def kill(self):
            Process.killed = True
    monkeypatch.setattr(subprocess, "Popen", Process)
    result = run_offline(case, world, tmp_path / "output", wall_seconds=1)
    assert result["verdict"] == "unscorable" and result["timed_out"] and Process.killed
    assert (tmp_path / "output/supervisor.json").is_file()
    with pytest.raises(FileExistsError):
        run_offline(case, world, tmp_path / "output")
