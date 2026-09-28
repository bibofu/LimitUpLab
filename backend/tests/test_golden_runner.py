"""Harness protocol tests with scripted models; never reported as Agent accuracy."""

import importlib.util
import json
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from evals.golden.contracts import Case, Expectation, Turn
from evals.golden.judge import judge_turn
from evals.golden.reporting import summarize
from evals.golden.runner import Budget, BudgetedProvider, BudgetExceeded, run_case


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


class ScriptedModel:
    model = "scripted-protocol-fixture"

    def generate_messages(self, messages, tools, **kwargs):
        only = tools[0]["function"]["name"] if len(tools) == 1 else None
        if only == "submit_input_security_review":
            return call(only, {"decision": "allow", "signals": [], "reason": "scripted fixture",
                              "request_kind": "research", "context_mode": "standalone"})
        if only == "submit_compliance_review":
            return call(only, {"decision": "allow", "violations": [], "reason": "scripted fixture"})
        if only == "submit_golden_judgements":
            payload = json.loads(messages[-1].content)
            return call(only, {"checks": [{"index": index, "passed": True, "reason": "scripted fixture"}
                                         for index in range(len(payload["requirements"]))]})
        observations = [json.loads(message.content) for message in messages if isinstance(message, ToolMessage)]
        if not observations:
            return call("limit_up_events", {"trade_date": "2026-09-22", "market": "main_board",
                                           "board_height": 1, "event_status": "closed"})
        key = observations[-1]["evidence_id"]
        return call("finish", {"status": "complete", "answer": "{{evidence_table}}", "evidence_ids": [key],
            "table": {"evidence_id": key, "columns": [{"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]}})


def example_case(**kwargs):
    expected = Expectation(columns=["symbol", "name"], rows=[
        ["600101", "华岳科技"], ["000202", "北辰制造"], ["000910", "中原农业"]],
        table_only=True, evidence_dates=["2026-09-22"])
    return Case(id="protocol_fixture", category="single", family="protocol", tags=["protocol"],
                turns=[Turn(user="2026-09-22主板首板，只列代码名称", expect=expected)], **kwargs)


def test_real_runtime_journal_and_independent_grader(tmp_path):
    model = ScriptedModel()
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=model, judge_provider=model)
    assert result["verdict"] == "pass", result["turns"][0]["checks"]
    turn = result["turns"][0]
    assert turn["response"]["run_id"].startswith("run_")
    assert any(event["event"] == "answer_delta" for event in turn["events"])
    assert turn["response"]["generated_by"].startswith("react-runtime-")
    assert len(list((tmp_path / "sessions").glob("*/session.sqlite"))) == 1


def test_missing_judge_is_review_not_pass(tmp_path):
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=ScriptedModel())
    assert result["verdict"] == "review"
    assert result["turns"][0]["checks"][-1]["name"] == "visible_answer_safety"


def test_sessions_are_persisted_and_isolated(tmp_path):
    case = example_case()
    case.turns += [case.turns[0].model_copy(update={"session": "other"}), case.turns[0].model_copy()]
    result = run_case(case, trial=1, directory=tmp_path, provider=ScriptedModel(), judge_provider=ScriptedModel())
    assert result["verdict"] == "pass"
    ids = [item["response"]["session_id"] for item in result["turns"]]
    assert ids[0] == ids[2] and ids[1] != ids[0]
    from app.repositories.chat_session_repository import SQLiteChatSessionRepository
    db = next((tmp_path / "sessions").glob("*/session.sqlite"))
    repository = SQLiteChatSessionRepository(db)
    assert len(repository.get_session(ids[0], owner_id="golden_owner").messages) == 4
    assert len(repository.get_session(ids[1], owner_id="golden_owner").messages) == 2
    assert repository.get_session(ids[0], owner_id="another_owner") is None


def test_budget_covers_every_provider_method():
    class Provider:
        def generate_messages(self): return "messages"
        def generate_function_call(self): return "memory"
        def generate(self): return "fallback"
    provider = BudgetedProvider(Provider(), Budget(2))
    assert provider.generate_messages() == "messages"
    assert provider.generate_function_call() == "memory"
    with pytest.raises(BudgetExceeded):
        provider.generate()


def test_report_counts_review_errors_and_incomplete_repeats():
    results = [{"case_id": "a", "category": "multi", "trial": 1, "verdict": "pass", "duration_seconds": 1, "turns": []},
               {"case_id": "a", "category": "multi", "trial": 2, "verdict": "fail", "duration_seconds": 2, "turns": []},
               {"case_id": "b", "category": "single", "trial": 1, "verdict": "review", "duration_seconds": 3, "turns": []},
               {"case_id": "c", "category": "single", "trial": 1, "verdict": "harness_error", "duration_seconds": 4, "turns": []}]
    summary = summarize(results, planned_trials=2)
    assert summary["trial_pass_rate"] == .25
    assert summary["fully_repeated_cases"] == 1
    assert summary["all_trials_passed_cases"] == 0
    assert summary["agent_tokens"] is None


def test_judge_requires_complete_unique_indices():
    class MissingJudge:
        def generate_messages(self, *args, **kwargs):
            return call("submit_golden_judgements", {"checks": []})
    from app.models import AgentChatResponse
    response = AgentChatResponse(session_id="test", intent="test", answer="hello", generated_by="fixture")
    with pytest.raises(ValueError, match="indices"):
        judge_turn(MissingJudge(), user="hello", expectations=[], response=response, drafts=[])


def test_validate_cli_never_loads_model(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/run_agent_golden.py"
    spec = importlib.util.spec_from_file_location("golden_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("No model in validation"))
    assert module.main(["--mode", "validate"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cases"] == 60
    assert report["smoke_cases"] == 12
