"""Synthetic worker protocol tests, never real-market Golden cases."""
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent_eval.candidates import save_summary_candidate
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.worker import execute_case
from app.agents.react_runtime import runtime
from app.models import AgentChatResponse
from app.services.prompt_security import PromptInjectionAssessment
from test_agent_eval_checks import artifact, no_external_calls


def assets(artifact, tmp_path):
    folder = tmp_path / "assets"
    save_summary_candidate(artifact, folder)
    case = load_case(folder / "case.json")
    case.conversation = [case.conversation[0].model_copy(update={"content": "查询协议测试对象"}),
                         case.conversation[0].model_copy(update={"content": "这只股票再查一次"})]
    case.expected_requirements = [case.expected_requirements[0].model_copy(update={
        "source_turn": 1, "source_text": case.conversation[1].content})]
    case.assertions = [case.assertions[-1].model_copy(update={"target": "answer.tool_contract", "expected": {"rule": "test-only"}})]
    (folder / "case.json").write_text(case.model_dump_json(), encoding="utf-8")
    world = load_world(folder / "world.json")
    world.recordings[0].observation.payload["items"] = [{"symbol": "600001", "name": "协议甲", "private_old_value": 123.45}]
    (folder / "world.json").write_text(world.model_dump_json(), encoding="utf-8")
    output = tmp_path / "run"
    output.mkdir()
    return folder, output


def test_two_real_runtime_calls_share_actual_context_and_refresh_evidence(artifact, tmp_path, monkeypatch):
    folder, output = assets(artifact, tmp_path)
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k: PromptInjectionAssessment(
        decision="allow", signals=[], reason="synthetic test", request_kind="research",
        context_mode="follow_up" if "再查" in k["message"] else "standalone"))
    class Model:
        model = "scripted-not-real"
        def generate_messages(self, messages, tools, **kwargs):
            observations = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            second = messages[-1].content == "这只股票再查一次" if not observations else "historical_entity_references\": [{" in messages[0].content
            if not observations:
                if second:
                    assert "协议甲" in messages[0].content
                    assert "first-answer-only-marker" not in str(messages)
                    assert "private_old_value" not in str(messages)
                    assert "123.45" not in str(messages)
                return AIMessage(content="", tool_calls=[{"name": "market_summary", "args": {}, "id": "q"}])
            key = observations[-1]["evidence_id"]
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "f", "args": {
                "status": "complete", "answer": "协议甲 second-answer" if second else "协议甲 first-answer-only-marker",
                "evidence_ids": [key]}}])
    report = execute_case(folder / "case.json", folder / "world.json", output, Model())
    read = lambda name: json.loads((output / name).read_text(encoding="utf-8"))
    first, second = read("turn-01/response.json"), read("turn-02/response.json")
    assert first["session_id"] == second["session_id"]
    assert read("turn-01/request.json")["message_id"] != read("turn-02/request.json")["message_id"]
    context = read("turn-02/context-before.json")
    assert context["messages"][1]["content"] == first["answer"]
    assert context["messages"][1]["metadata"] == first
    assert context["memory"] is None  # Production does not summarize only two messages.
    assert len(read("turn-02/session-after.json")["messages"]) == 4
    evidence = lambda r: next(t["output"]["evidence"] for t in r["tool_results"] if t["name"] == "react_execution")
    assert set(evidence(first)).isdisjoint(evidence(second))
    assert all(r["evidence_scope"] == "current_run" for r in evidence(second).values())
    assert report["completed_turns"] == 2 and report["model_calls"] == 4
    assert [t["model_calls"] for t in report["turns"]] == [2, 2]
    assert report["verdict"] == "needs_review" and not report["release_eligible"]
    assert read("response.json") == second


@pytest.mark.parametrize("problem", ["assistant", "three_turns", "first_turn_contract", "judge"])
def test_rejects_unsupported_or_fabricated_history_before_model(artifact, tmp_path, problem):
    folder, output = assets(artifact, tmp_path)
    case = load_case(folder / "case.json")
    if problem == "assistant": case.conversation[0].role = "assistant"
    if problem == "three_turns": case.conversation.insert(0, case.conversation[0].model_copy())
    if problem == "first_turn_contract":
        case.expected_requirements[0].source_turn = 0
        case.expected_requirements[0].source_text = case.conversation[0].content
    (folder / "case.json").write_text(case.model_dump_json(), encoding="utf-8")
    class Never:
        def generate_messages(self, *a, **k): raise AssertionError("must not call model")
    with pytest.raises(ValueError):
        execute_case(folder / "case.json", folder / "world.json", output, Never(), allow_judge=problem == "judge")


def test_first_turn_provider_failure_preserves_context_and_does_not_grade_second(artifact, tmp_path, monkeypatch):
    folder, output = assets(artifact, tmp_path)
    calls = []
    def failed(request, *a, **k):
        calls.append(request)
        return AgentChatResponse(session_id=request.session_id, intent="test", answer="provider failed",
            generated_by="test", task_status="error", stop_reason="provider_error")
    monkeypatch.setattr(runtime, "run", failed)
    class Never:
        def generate_messages(self, *a, **k): raise AssertionError("must not call model")
    report = execute_case(folder / "case.json", folder / "world.json", output, Never())
    assert len(calls) == 1 and report["completed_turns"] == 1 and report["evaluated_turn"] is None
    assert report["verdict"] == "unscorable" and report["primary_cause"] == "provider_failure"
    assert (output / "turn-01/session-after.json").exists()
    assert not (output / "turn-02").exists() and not (output / "trajectory.json").exists()
