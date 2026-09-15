"""Scripted providers validate the harness, never claim real-model quality."""

import json
import socket
import sqlite3
from datetime import datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent_eval.candidates import save_summary_candidate, summary_candidate
from app.agent_eval.evaluators import evaluate_trajectory_terminal
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.loader import load_suite
from app.agent_eval.local_capture import LocalSummaryRegistry
from app.agent_eval.models import AssertionSpec, CaseSpec
from app.agent_eval.recorder import capture_tool
from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.models import AgentChatRequest
from app.services.prompt_security import PromptInjectionAssessment
from app.services.sample_data import SAMPLE_EVENTS


ANCHOR = datetime.fromisoformat("2026-09-11T18:00:00+08:00")


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("deterministic harness must not use network or database")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **k: ComplianceReview(
        decision="allow", violations=[], reason="scripted contract test only"))
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k: PromptInjectionAssessment(
        decision="allow", signals=[], reason="scripted contract test only"))


@pytest.fixture
def artifact():
    events = [event.model_copy(update={"trade_date": ANCHOR.date()}) for event in SAMPLE_EVENTS]
    return capture_tool(LocalSummaryRegistry(events), tool="market_summary", arguments={},
                        anchor_datetime=ANCHOR, recording_id="synthetic-summary",
                        provenance="production tool over synthetic test input, not market facts",
                        source_manifest={"test_only": True})


def invoke(world, *, tool="market_summary", args=None, status="complete", missing=None, answer=None):
    class ScriptedProvider:
        calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1 and tool:
                return AIMessage(content="", tool_calls=[{"name": tool, "args": args or {}, "id": "query"}])
            views = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            evidence_ids = [v["evidence_id"] for v in views if "evidence_id" in v]
            if answer is not None:
                return AIMessage(content="", tool_calls=[{"name": "finish", "id": "finish",
                    "args": {"status": status, "answer": answer, "missing": missing or []}}])
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "finish",
                "args": {"status": status, "answer": "本地研究查询已完成。", "missing": missing or [],
                         "evidence_ids": evidence_ids}}])
    registry = FrozenAgentToolRegistry(world)
    # Fresh identities ensure repeated tests cannot measure journal idempotency.
    request = AgentChatRequest(session_id=str(uuid4()), message_id=str(uuid4()), message="查询本地涨停家数")
    with registry.anchored():
        return runtime.run(request, registry, ScriptedProvider())


def evaluate(case, response):
    return evaluate_trajectory_terminal(case, response, profile=case.profile)


def by_id(report):
    return {item.assertion_id: item.verdict for item in report.findings}


def test_real_runtime_trace_to_checks_never_confuses_tool_success_with_answer_correctness(artifact):
    case, world = summary_candidate(artifact)
    response = invoke(world)
    report = evaluate(case, response)
    assert by_id(report) == {"summary-tool": "pass", "local-only": "pass", "summary-facts": "needs_review",
                             "$terminal": "pass", "$missing": "pass"}
    assert report.verdict == "needs_review" and not report.release_eligible
    assert not any(t.name == "update_task" for t in response.tool_results)


@pytest.mark.parametrize("status", ["partial", "empty", "clarify", "refuse"])
def test_terminal_mismatch_is_fail_even_with_unimplemented_fact_check(artifact, status):
    case, world = summary_candidate(artifact)
    assert evaluate(case, invoke(world, status=status)).verdict == "fail"


@pytest.mark.parametrize("expected", ["partial", "clarify", "refuse"])
def test_false_complete_is_explicit_failure(artifact, expected):
    case, world = summary_candidate(artifact)
    case.expected_terminal.allowed_status = [expected]
    assert by_id(evaluate(case, invoke(world)))["$terminal"] == "fail"


def test_missing_required_tool_even_when_ui_tool_calls_claim_success(artifact):
    case, world = summary_candidate(artifact)
    response = invoke(world, tool=None, answer="本地涨停数量为40。")
    response.tool_calls = ["market_summary"]
    assert by_id(evaluate(case, response))["summary-tool"] == "fail"


def test_forbidden_attempt_includes_profile_policy_rejection(artifact):
    case, world = summary_candidate(artifact)
    case.assertions.append(AssertionSpec(id="no-web", evaluator="trajectory", kind="tool_forbidden",
        requirement_id="summary", target="web_search"))
    response = invoke(world, tool="web_search", args={"query": "test"})
    assert any(t.name == "react_policy" and t.output["decision"] == "reject" for t in response.tool_results)
    assert by_id(evaluate(case, response))["no-web"] == "fail"


def test_material_argument_checks_native_decision_not_tool_normalized_input(artifact):
    case, world = summary_candidate(artifact)
    case.assertions.append(AssertionSpec(id="date", evaluator="trajectory", kind="argument_equals",
        requirement_id="summary", target="market_summary.requested_as_of", expected="2026-09-11"))
    response = invoke(world, args={"requested_as_of": "2026-09-11"})
    business = next(t for t in response.tool_results if t.name == "market_summary")
    assert "requested_as_of" not in business.input
    assert by_id(evaluate(case, response))["date"] == "pass"
    case.assertions[-1].expected = "2026-09-10"
    assert by_id(evaluate(case, response))["date"] == "fail"


@pytest.mark.parametrize("change", ["version", "execution", "decision", "policy", "status"])
def test_incomplete_or_incompatible_trace_is_not_agent_failure_or_pass(artifact, change):
    case, world = summary_candidate(artifact)
    response = invoke(world)
    if change == "version":
        response.generated_by = "retired-runtime"
    elif change == "status":
        response.task_status = "error"
    else:
        response.tool_results = [t for t in response.tool_results if t.name != "react_" + change]
    report = evaluate(case, response)
    assert report.verdict == "needs_review"
    assert by_id(report)["summary-tool"] == "needs_review"


def test_missing_semantics_and_unknown_assertions_abstain(artifact):
    case, world = summary_candidate(artifact)
    response = invoke(world, missing=["涨停数量"])
    assert by_id(evaluate(case, response))["$missing"] == "needs_review"
    case.assertions[0].target = "typo_tool"
    assert by_id(evaluate(case, response))["summary-tool"] == "needs_review"
    case.assertions[1].target = "market_summary.typo_argument"
    assert by_id(evaluate(case, response))["local-only"] == "needs_review"


def test_candidate_export_is_versioned_unreviewed_and_exclusive(artifact, tmp_path):
    destination = tmp_path / "candidate"
    manifest = save_summary_candidate(artifact, destination)
    cases, worlds = load_suite([destination / "case.json"], [destination / "world.json"])
    assert cases[0].status == "candidate"
    assert worlds[0].recordings[0].observation.payload == artifact.body.recording.observation.payload
    assert manifest["source_capture_checksum"] == artifact.checksum
    assert manifest["review_status"] == "unreviewed" and not manifest["release_eligible"]
    before = (destination / "world.json").read_bytes()
    with pytest.raises(FileExistsError):
        save_summary_candidate(artifact, destination)
    assert (destination / "world.json").read_bytes() == before


def test_modified_capture_cannot_be_promoted(artifact, tmp_path):
    artifact.body.recording.observation.payload["limit_up_count"] = 999
    with pytest.raises(ValueError):
        save_summary_candidate(artifact, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_bad_expected_argument_is_evaluator_review_not_agent_fail(artifact):
    case, world = summary_candidate(artifact)
    case.assertions[1].expected = "false"
    assert by_id(evaluate(case, invoke(world)))["local-only"] == "needs_review"


def test_case_cannot_shadow_builtin_finding_ids(artifact):
    case, _ = summary_candidate(artifact)
    data = case.model_dump(mode="json")
    data["assertions"][0]["id"] = "$terminal"
    with pytest.raises(ValueError, match="reserved"):
        CaseSpec.model_validate(data)


def test_each_attempt_is_checked_not_only_last_correct_call(artifact):
    case, world = summary_candidate(artifact)
    response = invoke(world)
    decision = next(t for t in response.tool_results if t.name == "react_decision")
    policy = next(t for t in response.tool_results if t.name == "react_policy")
    decision.output["tool_calls"].append({"name": "market_summary", "id": "bad", "args": {"include_limit_down": True}})
    extra = policy.model_copy(deep=True)
    extra.output = {"call_id": "bad", "decision": "reject"}
    response.tool_results.append(extra)
    assert by_id(evaluate(case, response))["local-only"] == "fail"


@pytest.mark.parametrize("mode,exit_code", [("review", 2), ("fail", 1), ("scope-pass", 0)])
def test_cli_reports_partial_scope_and_nonzero_for_abstention(artifact, tmp_path, monkeypatch, capsys, mode, exit_code):
    from app.agent_eval.__main__ import main

    case, world = summary_candidate(artifact)
    if mode == "scope-pass":
        case.assertions = case.assertions[:2]
    response = invoke(world, status="partial" if mode == "fail" else "complete")
    case_path, response_path = tmp_path / "case.json", tmp_path / "response.json"
    case_path.write_text(case.model_dump_json(), encoding="utf-8")
    response_path.write_text(response.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["agent_eval", "check-response", "--case", str(case_path),
                        "--response", str(response_path), "--profile", "v1_close_review"])
    assert main() == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report["scope"] == "trajectory_terminal" and not report["release_eligible"]
