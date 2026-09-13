"""Process checks use trace causality, not the correctness of answer text."""

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent_eval.process_checks import evaluate_process
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agents.react_runtime import runtime
from app.models import AgentChatRequest
from test_agent_eval_checks import artifact, no_external_calls
from test_agent_eval_event_facts import bundle


def execute(bundle):
    case, world, _, _ = bundle
    class Provider:
        calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{"id": "source", "name": "limit_up_events",
                    "args": world.recordings[0].arguments}])
            if self.calls == 2:
                view = next(json.loads(m.content) for m in messages if isinstance(m, ToolMessage))
                return AIMessage(content="", tool_calls=[{"id": "compute", "name": "compute_result",
                    "args": {"evidence_id": view["evidence_id"], "operation": "select",
                             "sort_by": "amount", "descending": True, "limit": 3}}])
            return AIMessage(content="名单查询完毕。")
    registry = FrozenAgentToolRegistry(world)
    with registry.anchored():
        response = runtime.run(AgentChatRequest(session_id=str(uuid4()), message_id=str(uuid4()),
            message="列出成交额前三只"), registry, Provider())
    return case, response


def test_compute_process_checked_even_without_correct_answer(bundle):
    case, response = execute(bundle)
    report = evaluate_process(case, response)
    assert report.verdict == "pass" and not report.release_eligible
    assert report.metrics["independent_select_replays"] == 1
    assert report.metrics["compute_attempts"] == 1
    assert report.metrics["repeated_signatures"] == 0


def test_no_mandatory_compute_or_read_route(bundle):
    assert evaluate_process(bundle[0], bundle[2]).verdict == "pass"


@pytest.mark.parametrize("damage", ["view", "missing_observation", "record", "arguments"])
def test_broken_trace_abstains(bundle, damage):
    case, response = execute(bundle)
    if damage == "view":
        trace = next(t for t in response.tool_results if t.name == "react_observe")
        trace.output["results"][0]["rows"][0]["symbol"] = "wrong"
    elif damage == "missing_observation":
        response.tool_results = [t for t in response.tool_results if t.name != "react_observe"]
    else:
        execution = next(t.output for t in response.tool_results if t.name == "react_execution")
        record = next(r for r in execution["evidence"].values() if r["tool"] == "compute_result")
        if damage == "record":
            record["payload"]["items"].reverse()
        else:
            record["arguments"]["limit"] = 1
    assert evaluate_process(case, response).verdict == "needs_review"


def test_same_round_dependency_not_mistaken_for_observed_source(bundle):
    case, response = execute(bundle)
    # Move the second decision before the first observation, keeping complete call/policy traces.
    decisions = [t for t in response.tool_results if t.name == "react_decision"]
    second = decisions[1]
    response.tool_results.remove(second)
    first_observation = next(i for i,t in enumerate(response.tool_results) if t.name == "react_observe")
    response.tool_results.insert(first_observation, second)
    report = evaluate_process(case, response)
    assert any(f.assertion_id == "dependency:compute" and f.verdict == "fail" for f in report.findings)
