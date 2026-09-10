import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agents.chat_eval_dataset import load_dev_dataset
from app.agents.chat_eval_runner_v2 import (
    FrozenToolFixture,
    judge_answer,
    load_latest_completed_report,
    run_chat_eval_suite,
    run_online_shadow_eval,
    select_eval_cases,
    write_completed_report,
)
from app.models import (
    AgentChatResponse,
    AgentRun,
    AgentToolPolicyAudit,
    AgentToolTrace,
)
from app.services.llm_provider import LLMProvider, LLMResult


class LiveEvalProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls += 1
        if "first job is to decide which tools are needed" in system_prompt:
            content = json.dumps(
                {
                    "intent_label": "limit_up_query",
                    "capabilities": ["limit_up_pool"],
                    "context_mode": "standalone",
                    "context_capabilities": [],
                    "safety": "normal",
                }
            )
            model = "planner-fixture"
        else:
            content = "冻结涨停样本已返回筛选后的名单。仅作研究，不构成投资建议。"
            model = "answer-fixture"
        return LLMResult(
            content=content,
            model=model,
            provider="test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


class JudgeProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        assert "不得使用自身知识" in system_prompt
        payload = json.loads(user_prompt)
        assert payload["tool_facts"]
        return LLMResult(
            content=json.dumps(
                {
                    "relevance": 2,
                    "completeness": 2,
                    "explanation": 2,
                    "uncertainty": 1,
                    "concision": 2,
                    "rationale": "grounded",
                }
            ),
            model="judge-pinned-v1",
            provider="test",
        )


class FailingProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        raise RuntimeError("provider unavailable")


class RunRepositoryStub:
    def __init__(self, runs: list[AgentRun]) -> None:
        self.runs = runs

    def list_runs(self, limit: int) -> list[AgentRun]:
        assert limit == 500
        return self.runs


def _limit_up_case():
    return load_dev_dataset().cases[27]


def test_fixture_covers_every_required_dev_tool():
    fixture = FrozenToolFixture()
    required = {
        tool
        for case in load_dev_dataset().cases
        for tool in case.expected.required_tools
    }
    assert required == set(fixture.tools)


def test_stratified_selection_is_repeatable_and_keeps_all_types():
    cases = load_dev_dataset().cases
    first = select_eval_cases(cases, sample_size=12, seed="night-1")
    second = select_eval_cases(cases, sample_size=12, seed="night-1")
    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert {case.primary_type for case in first} == {
        "capability_base",
        "multi_turn",
        "composite",
        "failure",
        "safety",
        "out_of_scope",
    }


def test_offline_replay_runs_all_dev_cases_and_planner_is_na():
    report = run_chat_eval_suite(
        load_dev_dataset().cases,
        mode="offline",
        trials=1,
        seed="offline",
    )
    assert report["case_count"] == 120
    assert report["passed_cases"] == 120
    assert report["planner_metrics"]["applicable_trials"] == 0
    assert report["capability_metrics"]["macro_f1"] is None
    assert report["fixture_snapshot_id"] == "chat-fixture-v2"


def test_live_mode_uses_real_planner_and_answer_on_frozen_tools():
    provider = LiveEvalProvider()
    report = run_chat_eval_suite(
        [_limit_up_case()],
        mode="live",
        trials=1,
        seed="live",
        llm_provider=provider,
    )
    result = report["results"][0]
    assert provider.calls == 2
    assert result["stages"]["planner"]["status"] == "pass"
    assert result["stages"]["execution"]["status"] == "pass"
    assert report["efficiency_metrics"]["token_p50"] == 30


def test_provider_failure_is_not_hidden_by_template_fallback():
    report = run_chat_eval_suite(
        [_limit_up_case()],
        mode="live",
        trials=1,
        seed="live",
        llm_provider=FailingProvider(),
    )
    assert report["provider_failure_rate"] == 1
    assert report["failed_cases"] == 1


def test_judge_receives_only_question_behavior_facts_and_answer():
    case = _limit_up_case()
    response = AgentChatResponse(
        session_id="judge",
        intent="eval",
        answer="已按冻结事实回答。",
        tool_calls=["limit_up_events"],
        tool_results=[
            FrozenToolFixture().trace(
                "limit_up_events", arguments={}, result_state="ok"
            )
        ],
        generated_by="test",
    )
    score = judge_answer(case, response, JudgeProvider())
    assert score.total == 9
    assert score.passed
    assert score.model == "judge-pinned-v1"


def test_completed_report_is_published_and_loaded_without_running_eval(tmp_path):
    report = run_chat_eval_suite(
        [_limit_up_case()], mode="offline", trials=1, seed="persist"
    )
    path = write_completed_report(report, output_root=tmp_path)
    assert path == tmp_path / report["run_id"] / "summary.json"
    assert (path.parent / "failures.json").is_file()
    assert load_latest_completed_report(tmp_path)["run_id"] == report["run_id"]


def test_online_shadow_uses_persisted_response_and_omits_question_from_report():
    now = datetime.now(timezone.utc)
    response = AgentChatResponse(
        session_id="shadow",
        intent="limit_up_query",
        answer="当前没有可用数据，因此无法给出事实结论。",
        tool_calls=["limit_up_events"],
        tool_results=[
            FrozenToolFixture().trace(
                "limit_up_events", arguments={}, result_state="empty"
            )
        ],
        tool_policy=AgentToolPolicyAudit(
            planner_tool_calls=["limit_up_events"],
            final_tool_calls=["limit_up_events"],
        ),
        generated_by="test",
    )
    run = AgentRun(
        run_id="private-run-id",
        session_id="shadow",
        run_type="agent_chat",
        status="success",
        intent="limit_up_query",
        tool_calls=response.tool_calls,
        input_json={"message": "这是匿名真实问题"},
        output_json=response.model_dump(mode="json"),
        started_at=now,
        finished_at=now,
    )
    report = run_online_shadow_eval(
        sample_size=1,
        seed="shadow",
        repository=RunRepositoryStub([run]),  # type: ignore[arg-type]
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert report["case_count"] == 1
    assert report["stage_metrics"]["planner"]["applicable_trials"] == 0
    assert "这是匿名真实问题" not in serialized
    assert "private-run-id" not in serialized
