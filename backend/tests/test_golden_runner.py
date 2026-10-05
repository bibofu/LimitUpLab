"""Harness protocol tests with scripted models; never reported as Agent accuracy."""

import importlib.util
import json
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from evals.golden.contracts import Case, Expectation, Turn
from evals.golden.judge import judge_turn
from evals.golden.reporting import record_trial, summarize
from evals.golden.runner import Budget, BudgetedProvider, BudgetExceeded, run_case, without_sdk_retries


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
        if only == "submit_golden_visible_audit":
            return call(only, {key: {"evidence_relation": "supported", "reason": "scripted fixture",
                "surface_id": None, "quote": None} for key in ("safety", "source", "factual")})
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


def test_runtime_metadata_is_captured_from_real_prompt_and_passed_to_each_judge_phase(tmp_path):
    class RecordingJudge(ScriptedModel):
        def __init__(self):
            self.payloads = []

        def generate_messages(self, messages, tools, **kwargs):
            self.payloads.append(json.loads(messages[-1].content))
            return super().generate_messages(messages, tools, **kwargs)

    case = example_case()
    case.turns[0].page_date = "2026-09-21"
    case.turns[0].expect.semantic_checks = ["最终应交付名单。"]
    judge = RecordingJudge()
    result = run_case(case, trial=1, directory=tmp_path, provider=ScriptedModel(), judge_provider=judge)
    turn = result["turns"][0]
    assert result["verdict"] == "pass", turn["checks"]
    metadata = turn["runtime_metadata"]
    assert len(metadata) == 1 and turn["runtime_metadata_errors"] == []
    assert metadata[0]["context"] == {"anchor_date": "2026-09-22", "page_default_date": "2026-09-21",
        "page_default_symbol": None, "available_local_dates": ["2026-09-18", "2026-09-21", "2026-09-22"]}
    assert len(judge.payloads) == 2
    assert all(payload["trusted_runtime_metadata"] == metadata for payload in judge.payloads)


def test_actual_rating_count_scope_reaches_both_judge_phases(tmp_path):
    from evals.golden.cases import load_cases

    class RatingModel(ScriptedModel):
        def generate_messages(self, messages, tools, **kwargs):
            names = {item["function"]["name"] for item in tools}
            if "finish" not in names:
                return super().generate_messages(messages, tools, **kwargs)
            observations = [json.loads(message.content) for message in messages if isinstance(message, ToolMessage)]
            if not observations:
                return call("first_board_ratings", {"trade_date": "2026-09-22"})
            return call("finish", {"status": "complete", "answer": "输入事件总体10条，本次返回候选4只。",
                                   "evidence_ids": [observations[-1]["evidence_id"]]})

    class RecordingJudge(ScriptedModel):
        def __init__(self):
            self.payloads = []

        def generate_messages(self, messages, tools, **kwargs):
            self.payloads.append(json.loads(messages[-1].content))
            return super().generate_messages(messages, tools, **kwargs)

    case = next(case for case in load_cases() if case.id == "s19_rating_denominators")
    judge = RecordingJudge()
    result = run_case(case, trial=1, directory=tmp_path, provider=RatingModel(), judge_provider=judge)
    assert result["verdict"] == "pass"
    assert len(judge.payloads) == 2
    for payload in judge.payloads:
        views = [item for item in payload["trusted_runtime_metadata"] if item["origin"] == "agent_evidence_view"]
        assert len(views) == 1
        assert views[0]["metadata"]["returned_candidate_count"] == 4
        assert views[0]["metadata"]["count_scope"]["symbols_filtered"] is False
        assert views[0]["evidence_id"] in payload["synthetic_evidence"]


def test_input_refusal_has_no_fabricated_runtime_snapshot(tmp_path):
    class RefusalModel(ScriptedModel):
        def generate_messages(self, messages, tools, **kwargs):
            if len(tools) == 1 and tools[0]["function"]["name"] == "submit_input_security_review":
                return call("submit_input_security_review", {"decision": "refuse", "signals": ["instruction_override"],
                    "reason": "scripted input refusal", "request_kind": "research", "context_mode": "standalone"})
            return super().generate_messages(messages, tools, **kwargs)
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=RefusalModel())
    turn = result["turns"][0]
    assert turn["response"]["task_status"] == "refuse"
    assert turn["runtime_metadata"] == [] and turn["runtime_metadata_errors"] == []


def test_missing_judge_is_review_not_pass(tmp_path):
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=ScriptedModel())
    assert result["verdict"] == "review"
    assert result["turns"][0]["checks"][-1]["name"] == "visible_answer_safety"


@pytest.mark.parametrize("source_verdict, expected", [(False, "fail"), (None, "review")])
def test_source_audit_applies_without_per_case_semantic_checks(tmp_path, source_verdict, expected):
    class SourceJudge(ScriptedModel):
        def generate_messages(self, messages, tools, **kwargs):
            if len(tools) == 1 and tools[0]["function"]["name"] == "submit_golden_visible_audit":
                payload = json.loads(messages[-1].content)
                final = next(s for s in payload["surfaces"] if s["surface_id"] == "final")
                decisions = {key: {"evidence_relation": "supported", "reason": "protocol fixture", "surface_id": None,
                    "quote": None} for key in ("safety", "source", "factual")}
                relation = "contradicted" if source_verdict is False else "insufficient_evidence"
                decisions["source"].update(evidence_relation=relation, reason="Scripted source-only finding",
                    surface_id="final", quote=final["text"].splitlines()[0])
                # Transport fixture only; a real judge must also establish logical relevance.
                evidence_id, record = next(iter(payload["synthetic_evidence"].items()))
                decisions["source"]["counterevidence"] = [{
                    "path": ["synthetic_evidence", evidence_id, "sources"], "value": record["sources"]}]
                return call("submit_golden_visible_audit", decisions)
            return super().generate_messages(messages, tools, **kwargs)
    result = run_case(example_case(), trial=1, directory=tmp_path,
                      provider=ScriptedModel(), judge_provider=SourceJudge())
    turn = result["turns"][0]
    assert result["verdict"] == expected
    assert turn["source_judgement"]["passed"] is source_verdict
    assert turn["source_judgement"]["evidence_relation"] == (
        "contradicted" if source_verdict is False else "insufficient_evidence")
    assert turn["factual_judgement"]["passed"] is True
    checks = {c["name"]: c["passed"] for c in turn["checks"]}
    assert checks["expected_values"] and checks["table_evidence_values"]
    assert checks["visible_source_attribution"] is source_verdict


def test_delivery_judge_error_does_not_erase_independent_audit_or_hard_fail(tmp_path):
    class BrokenDelivery(ScriptedModel):
        def generate_messages(self, messages, tools, **kwargs):
            if len(tools) == 1 and tools[0]["function"]["name"] == "submit_golden_judgements":
                raise ValueError("private-provider-error-text")
            return super().generate_messages(messages, tools, **kwargs)
    case = example_case()
    case.turns[0].expect.semantic_checks = ["Deliver the required list."]
    case.turns[0].expect.rows = [["999999", "independent wrong-row diagnostic"]]
    result = run_case(case, trial=1, directory=tmp_path,
                      provider=ScriptedModel(), judge_provider=BrokenDelivery())
    turn = result["turns"][0]
    assert result["verdict"] == "fail"  # Unknown semantic judgement cannot overwrite hard failure.
    assert turn["judge_errors"] == {"final_delivery": "ValueError"}
    assert turn["judge_diagnostics"]["final_delivery"] == {"failure_category": "request"}
    assert turn["judge_diagnostics"]["visible_audit"]["tool_call_count"] == 1
    assert turn["judgements"] is None and turn["safety_judgement"]["passed"]
    assert turn["factual_judgement"]["passed"] and turn["source_judgement"]["passed"]
    assert "private-provider-error-text" not in json.dumps(result)


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
    summary = summarize(results, planned_trials=2, planned_cases=3)
    assert summary["trial_pass_rate"] == .25
    assert summary["fully_repeated_cases"] == 1
    assert summary["all_trials_passed_cases"] == 0
    assert summary["agent_tokens"] is None
    assert summary["execution_complete"] is False
    assert summary["planned_trials"] == 6


def test_harness_budget_exhaustion_is_not_model_failure(tmp_path):
    provider = BudgetedProvider(ScriptedModel(), Budget(1))
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=provider)
    assert result["verdict"] == "harness_error"
    assert "budget exhausted" in result["error"]
    assert result["completed"] is False
    assert result["stop_reason"] == "model_call_budget"


def test_judge_requires_complete_unique_indices():
    class MissingJudge:
        def generate_messages(self, *args, **kwargs):
            return call("submit_golden_judgements", {"checks": []})
    from app.models import AgentChatResponse
    response = AgentChatResponse(session_id="test", intent="test", answer="hello", generated_by="fixture")
    review = judge_turn(MissingJudge(), user="hello", expectations=["Answer the greeting"],
                        response=response, drafts=[])
    assert review.judgements is None
    assert review.errors["final_delivery"] == "ValueError"
    assert review.safety is None and review.source is None and review.factual is None


def cli_module():
    path = Path(__file__).resolve().parents[1] / "scripts/run_agent_golden.py"
    spec = importlib.util.spec_from_file_location("golden_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_cli_never_loads_model(monkeypatch, capsys):
    module = cli_module()
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("No model in validation"))
    assert module.main(["--mode", "validate"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cases"] == 61
    assert report["smoke_cases"] == 12
    assert report["profile"] == "v1_close_review"
    assert "remote_limit_up_pool" not in report["enabled_tools"]
    assert "market_index_trend" in report["fixture_tools"]


def test_validate_extended_profile_keeps_full_catalog_and_explicit_fixture_gaps(monkeypatch, capsys):
    module = cli_module()
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("No model in validation"))
    assert module.main(["--mode", "validate", "--profile", "extended"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["profile"] == "extended"
    assert "remote_limit_up_pool" in report["fixture_tools"]
    assert "web_search" in report["fixture_tools"]
    assert "rating_evaluation" in report["uncovered_tools"]


@pytest.mark.parametrize("profile,allowed", [("v1_close_review", False), ("extended", True)])
def test_runner_exposes_selected_profile_to_actual_agent_calls(tmp_path, profile, allowed):
    class ProfileModel(ScriptedModel):
        observed = []

        def generate_messages(self, messages, tools, **kwargs):
            names = {item["function"]["name"] for item in tools}
            if "limit_up_events" in names:
                self.observed.append(names)
            return super().generate_messages(messages, tools, **kwargs)
    model = ProfileModel()
    model.observed = []
    result = run_case(example_case(), trial=1, directory=tmp_path, provider=model,
                      judge_provider=ScriptedModel(), profile=profile)
    assert result["verdict"] == "pass"
    assert model.observed
    assert all(("remote_limit_up_pool" in names) is allowed for names in model.observed)


def test_budget_interrupted_trial_resumes_without_double_counting(tmp_path, monkeypatch):
    module = cli_module()
    case = example_case()
    case.category = "multi"
    case.turns.append(case.turns[0].model_copy(deep=True))
    observed_reservations = []
    class CheckingModel(ScriptedModel):
        def generate_messages(self, *args, **kwargs):
            saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
            observed_reservations.append(saved["model_calls_used"])
            return super().generate_messages(*args, **kwargs)
    monkeypatch.setattr(module, "load_cases", lambda: [case])
    monkeypatch.setattr(module, "code_fingerprint", lambda: "fixed-test-code")
    monkeypatch.setattr("app.config.configure_runtime_environment", lambda: None)
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", CheckingModel)
    arguments = ["--mode", "live", "--case", case.id, "--judge", "model", "--output", str(tmp_path)]
    assert module.main([*arguments, "--max-model-calls", "5"]) == 2
    first = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert first["results"][0]["turns"][0]["verdict"] == "pass"
    assert first["results"][0]["completed"] is False
    assert first["summary"]["execution_complete"] is False
    assert first["summary"]["fully_repeated_cases"] == 0
    assert first["model_calls_used"] == 5
    assert module.main([*arguments, "--resume", "--max-model-calls", "30"]) == 0
    resumed = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert len(resumed["results"]) == 1
    assert resumed["results"][0]["completed"] is True
    assert len(resumed["results"][0]["turns"]) == 2
    assert len(resumed["attempt_history"]) == 1
    assert resumed["attempt_history"][0]["verdict"] == "harness_error"
    assert resumed["summary"]["attempted_trials"] == 1
    assert resumed["summary"]["recorded_attempts"] == 2
    assert resumed["summary"]["execution_complete"] is True
    assert resumed["model_calls_used"] == 15
    assert observed_reservations == list(range(1, 16))
    assert len(list((tmp_path / "sessions").glob("*/session.sqlite"))) == 2
    assert module.main([*arguments, "--resume", "--max-model-calls", "30"]) == 0
    assert observed_reservations == list(range(1, 16))  # A completed trial is never paid for again.


def test_effective_settings_are_hashed_and_sdk_retries_disabled():
    from copy import copy
    from types import SimpleNamespace
    class Client:
        def __init__(self, retries):
            self.max_retries = retries
            self.base_url = "https://private-user:private-password@example.invalid/v1?token=private-token"
            self.timeout = 31
            self.chat = SimpleNamespace(completions=object())
        def with_options(self, **kwargs):
            clone = copy(self)
            clone.__dict__.update(kwargs)
            return clone
    class Model(SimpleNamespace):
        def model_copy(self, update):
            clone = copy(self)
            clone.__dict__.update(update)
            return clone
    model = Model(root_client=Client(3), root_async_client=Client(3), max_retries=3,
                  openai_api_base="https://private-user:private-password@example.invalid/v1",
                  extra_body={"thinking": {"type": "disabled"}}, request_timeout=31)
    original = SimpleNamespace(model="fixture", chat_model=model, planner_max_tokens=1024,
                               native_function_calling_enabled=True, api_key="private-api-key")
    configured = without_sdk_retries(original)
    assert original.chat_model.max_retries == 3
    assert original.chat_model.root_client.max_retries == 3
    assert configured.chat_model.max_retries == 0
    assert configured.chat_model.root_client.max_retries == 0
    assert configured.chat_model.root_async_client.max_retries == 0
    module = cli_module()
    initial = module.provider_configuration(configured)
    serialized = json.dumps(initial)
    assert not any(secret in serialized for secret in (
        "private-user", "private-password", "private-token", "private-api-key", "example.invalid",
    ))
    configured.api_key = "rotated-api-key"
    assert module.provider_configuration(configured) == initial
    configured.chat_model.extra_body = {"thinking": {"type": "enabled"}}
    assert module.provider_configuration(configured) != initial
    thinking = module.provider_configuration(configured)
    configured.chat_model.root_client.base_url = "https://different-endpoint.invalid/v1"
    assert module.provider_configuration(configured) != thinking


def test_resume_rejects_changed_effective_configuration(tmp_path, monkeypatch):
    module = cli_module()
    case = example_case()
    configuration = {"settings_sha256": "thinking-disabled"}
    monkeypatch.setattr(module, "load_cases", lambda: [case])
    monkeypatch.setattr(module, "code_fingerprint", lambda: "fixed-test-code")
    monkeypatch.setattr(module, "provider_configuration", lambda _: dict(configuration))
    monkeypatch.setattr("app.config.configure_runtime_environment", lambda: None)
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", ScriptedModel)
    arguments = ["--mode", "live", "--case", case.id, "--judge", "model", "--output", str(tmp_path)]
    assert module.main(arguments) == 0
    configuration["settings_sha256"] = "thinking-enabled"
    with pytest.raises(SystemExit) as error:
        module.main([*arguments, "--resume"])
    assert error.value.code == 2


def test_resume_rejects_changed_profile(tmp_path, monkeypatch):
    module = cli_module()
    case = example_case()
    monkeypatch.setattr(module, "load_cases", lambda: [case])
    monkeypatch.setattr("app.config.configure_runtime_environment", lambda: None)
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", ScriptedModel)
    arguments = ["--mode", "live", "--case", case.id, "--judge", "model", "--output", str(tmp_path)]
    assert module.main(arguments) == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["manifest"]["profile"] == "v1_close_review"
    with pytest.raises(SystemExit) as error:
        module.main([*arguments, "--resume", "--profile", "extended"])
    assert error.value.code == 2


def test_configuration_hash_includes_direct_provider_request_options():
    from app.services.llm_provider import OpenAIChatCompletionsProvider
    module = cli_module()
    # Legacy text-provider attributes are hashed without enabling it for ReAct.
    provider = OpenAIChatCompletionsProvider(api_key="private-fixture-key",
        base_url="https://user:password@fixture.invalid/v1", thinking_enabled=False)
    previous = module.provider_configuration(provider)
    for attribute, value in (("thinking_enabled", True), ("timeout_seconds", 47),
                             ("planner_max_tokens", 765), ("max_attempts", 4),
                             ("base_url", "https://another.invalid/v1")):
        setattr(provider, attribute, value)
        current = module.provider_configuration(provider)
        assert current != previous, attribute
        previous = current
    assert "private-fixture-key" not in json.dumps(previous)
    assert "fixture.invalid" not in json.dumps(previous)


def test_memory_function_call_makes_one_http_attempt_after_rate_limit():
    import httpx
    from app.services.langchain_provider import AuditedChatOpenAI, LangChainChatProvider
    requests = []
    def limited(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "synthetic rate limit",
            "type": "rate_limit_error", "code": "rate_limit_exceeded"}})
    with httpx.Client(transport=httpx.MockTransport(limited)) as client:
        chat = AuditedChatOpenAI(model="fixture", api_key="synthetic-key",
            base_url="https://fixture.invalid/v1", max_retries=2, http_client=client)
        provider = LangChainChatProvider(api_key="synthetic-key", model="fixture",
            base_url="https://fixture.invalid/v1", timeout_seconds=1, planner_max_tokens=128,
            thinking_enabled=False, max_attempts=3, native_function_calling_enabled=True,
            chat_model=chat)
        configured = without_sdk_retries(provider)
        with pytest.raises(RuntimeError, match="RateLimitError"):
            configured.generate_function_call("system", "memory fixture", function_name="memory",
                function_description="fixture", parameters={"type": "object", "properties": {}})
    assert len(requests) == 1


def test_incomplete_attempt_history_does_not_inflate_rates_or_drop_usage():
    interrupted = {"case_id": "a", "trial": 1, "category": "multi", "verdict": "harness_error",
                   "completed": False, "duration_seconds": 2, "turns": [{"agent_usage": {"total_tokens": 10}}]}
    finished = {**interrupted, "completed": True, "verdict": "pass",
                "turns": [{"agent_usage": {"total_tokens": 20}}]}
    report = {"results": [interrupted]}
    assert summarize(report["results"], planned_trials=1, planned_cases=1)["execution_complete"] is False
    record_trial(report, finished)
    summary = summarize(report["results"], planned_trials=1, planned_cases=1,
                        attempt_history=report["attempt_history"])
    assert summary["attempted_trials"] == 1
    assert summary["trial_pass_rate"] == 1
    assert summary["agent_tokens"] == 30
    with pytest.raises(ValueError, match="completed"):
        record_trial(report, finished)
    with pytest.raises(ValueError, match="Duplicate"):
        summarize([finished, finished], planned_trials=1)
