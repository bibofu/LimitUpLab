"""Replay infrastructure tests use scripted judges, never new Agent/model runs."""

from copy import deepcopy
import hashlib
import json

from langchain_core.messages import AIMessage
import pytest

from app.models import AgentChatResponse, AgentToolTrace
from app.services.llm_provider import LLMResult, NativeFunctionCallingError, _usage_tracker
from evals.golden import replay
from evals.golden.contracts import Case, Check, Expectation, Turn
from evals.golden.reporting import digest
from scripts import replay_agent_golden_judge as cli


@pytest.fixture
def source_fixture(tmp_path, monkeypatch):
    expect = Expectation(statuses=["error"], require_evidence=False,
                         semantic_checks=["Explicitly disclose the failed query."])
    case = Case(id="replay_fixture", category="multi", family="replay", tags=["synthetic"],
                turns=[Turn(user="Query synthetic data", expect=expect),
                       Turn(user="Query again", expect=expect.model_copy(update={"semantic_checks": []}))])
    results = []
    for trial in (1, 2):
        turns = []
        for index, turn in enumerate(case.turns):
            response = AgentChatResponse(session_id=f"golden-{case.id}-t{trial}-main",
                intent="research", answer="Synthetic query failed.", task_status="error",
                stop_reason="provider_error", generated_by="original-agent", tool_calls=["limit_up_events"],
                tool_results=[AgentToolTrace(name="limit_up_events", summary="Frozen failure", status="error",
                    input={"trade_date": "2026-09-22"}, output={"execution_status": "failed"}),
                    AgentToolTrace(name="react_execution", summary="Frozen evidence", output={"evidence": {}})])
            turns.append({"index": index, "user": turn.user, "session": turn.session,
                "expected": turn.expect.model_dump(mode="json"), "response": response.model_dump(mode="json"),
                "events": [{"event": "answer_delta", "payload": {"offset": 0, "delta": "Withdrawn draft"}},
                           {"event": "answer_reset", "payload": {}},
                           {"event": "answer_delta", "payload": {"offset": 0, "delta": response.answer}}],
                "runtime_metadata": [{"origin": "agent_system_message", "context": {"anchor_date": "2026-09-22"}}],
                "checks": [Check(name="historical_judge", passed=None).model_dump()], "verdict": "review",
                "factual_judgement": {"passed": None, "reason": "Historical unresolved result"},
                "agent_usage": {"call_count": 99, "total_tokens": 9000},
                "judge_usage": {"call_count": 88, "total_tokens": 8000}, "judge_errors": {"old": "Unresolved"}})
        results.append({"case_id": case.id, "trial": trial, "definition": case.model_dump(mode="json"),
                        "completed": True, "planned_turns": 2, "turns": turns, "verdict": "review"})
    original = {"mode": "live-model-frozen-tools", "manifest": {"case_ids": [case.id], "trials": 2,
        "dataset_hash": digest([case.model_dump(mode="json")]), "git_commit": "original-commit",
        "production_hash": "original-production", "evaluator_hash": "original-evaluator"},
        "results": results, "summary": {"historical": True}}
    source = tmp_path / "source.json"
    source.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(replay, "load_cases", lambda: [case])
    def forbidden(*args, **kwargs):
        pytest.fail("Replay must never invoke the Agent")
    monkeypatch.setattr("evals.golden.runner.run_case", forbidden)
    return source, original, case


class Judge:
    model = "scripted-replay"

    def __init__(self, *, invalid=False, unknown=False, interrupt=False):
        self.payloads, self.invalid, self.unknown, self.interrupt = [], invalid, unknown, interrupt

    def generate_messages(self, messages, tools, **kwargs):
        payload = json.loads(messages[-1].content)
        self.payloads.append(payload)
        tracker = _usage_tracker.get()
        tracker.begin_call(self.model)
        if self.interrupt:
            tracker.fail_call()
            raise KeyboardInterrupt
        tracker.complete_call(LLMResult(content="", model=self.model, provider="scripted",
            prompt_tokens=10, completion_tokens=2, total_tokens=12))
        if self.invalid and payload["phase"] == "visible_audit":
            tracker.fail_call()
            raise NativeFunctionCallingError("secret must not be retained", diagnostics={"failure_category": "tool_arguments"})
        if payload["phase"] == "final_delivery":
            args = {"checks": [{"index": index, "passed": None if self.unknown else True,
                                "reason": "Scripted judgement"} for index in range(len(payload["requirements"]))]}
        else:
            args = {key: {"reason": "Scripted empty claims", "claims": []}
                    for key in ("factual", "source", "safety")}
        return AIMessage(content="", tool_calls=[{"id": "judge-call", "name": tools[0]["function"]["name"], "args": args}])


def test_replay_preserves_all_recorded_inputs_and_separates_usage(source_fixture, tmp_path, monkeypatch):
    source, original, _ = source_fixture
    before = source.read_bytes()
    model = Judge()
    observed_inputs = []
    judge = replay.judge_turn
    def observe(provider, **kwargs):
        observed_inputs.append(deepcopy(kwargs))
        return judge(provider, **kwargs)
    monkeypatch.setattr(replay, "judge_turn", observe)
    report = replay.replay_report(source, tmp_path / "replay", model, max_calls=6)
    assert source.read_bytes() == before
    assert report["source"]["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["source"]["generation_manifest"] == original["manifest"]
    assert report["mode"] == "judge-replay" and "matched_settings" not in report
    assert report["judge_manifest"]["evaluator_hash"] != "original-evaluator"
    assert report["summary"]["execution_complete"]
    assert report["summary"]["trial_verdict_counts"] == {"pass": 2}
    assert report["summary"]["new_agent_calls"] == 0
    assert report["summary"]["new_judge_usage"]["call_count"] == 6
    assert report["summary"]["new_judge_usage"]["total_tokens"] == 72
    assert report["logical_calls_used"] == 6
    originals = [t for r in original["results"] for t in r["turns"]]
    assert [i["source_turn"] for i in report["inputs"]] == originals
    assert all(item["drafts"] == ["Withdrawn draft", "Synthetic query failed."] for item in observed_inputs)
    for payload in model.payloads:
        assert "expected" not in payload and "expected_labels" not in payload
        assert payload["trusted_runtime_metadata"] == originals[0]["runtime_metadata"]
        assert payload["business_observations"][0]["output"] == {"execution_status": "failed"}
        if payload["phase"] == "visible_audit":
            assert {s["text"] for s in payload["surfaces"]} == {"Synthetic query failed.", "Withdrawn draft"}
    assert json.loads((tmp_path / "replay/report.json").read_text(encoding="utf-8")) == report


def test_select_exact_turn_does_not_claim_whole_trial_scored(source_fixture, tmp_path):
    source, _, _ = source_fixture
    selected = [("replay_fixture", 2, 1)]
    report = replay.replay_report(source, tmp_path / "subset", Judge(), selected_turns=selected)
    assert [i["key"] for i in report["inputs"]] == [list(selected[0])]
    assert report["summary"]["completed_turns"] == 1
    assert report["summary"]["fully_scored_source_trials"] == 0
    assert report["logical_calls_used"] == 1


@pytest.mark.parametrize("invalid", [True, False])
def test_unknown_and_protocol_failure_remain_review(source_fixture, tmp_path, invalid):
    source, _, _ = source_fixture
    report = replay.replay_report(source, tmp_path / "review", Judge(invalid=invalid, unknown=not invalid),
                                  selected_turns=[("replay_fixture", 1, 0)])
    assert report["results"][0]["verdict"] == "review"
    assert report["summary"]["execution_complete"]
    assert "secret must not be retained" not in json.dumps(report)
    assert report["summary"]["new_judge_usage"]["failed_call_count"] == int(invalid)


def test_structured_hard_failure_not_erased_by_positive_judge(source_fixture, tmp_path):
    source, original, _ = source_fixture
    original["results"][0]["turns"][0]["response"]["task_status"] = "cancelled"
    source.write_text(json.dumps(original), encoding="utf-8")
    report = replay.replay_report(source, tmp_path / "hard-fail", Judge(), selected_turns=[("replay_fixture", 1, 0)])
    assert report["results"][0]["verdict"] == "fail"
    assert any(c["name"] == "status" and c["passed"] is False for c in report["results"][0]["checks"])


@pytest.mark.parametrize("change", ["duplicate", "incomplete", "oracle", "dataset", "session", "verdict", "missing_metadata"])
def test_invalid_source_rejected_before_calls(source_fixture, tmp_path, change):
    source, original, _ = source_fixture
    if change == "duplicate":
        original["results"].append(deepcopy(original["results"][0]))
    elif change == "incomplete":
        original["results"][0]["completed"] = False
    elif change == "oracle":
        original["results"][0]["definition"]["turns"][0]["user"] = "Different user"
    elif change == "dataset":
        original["manifest"]["dataset_hash"] = "wrong"
    elif change == "session":
        original["results"][0]["turns"][0]["response"]["session_id"] = "other"
    elif change == "verdict":
        original["results"][0]["turns"][0]["verdict"] = "pass"
    else:
        del original["results"][0]["turns"][0]["runtime_metadata"]
    source.write_text(json.dumps(original), encoding="utf-8")
    model = Judge()
    with pytest.raises((ValueError, KeyError)):
        replay.replay_report(source, tmp_path / "invalid", model)
    assert not model.payloads and not (tmp_path / "invalid").exists()


def test_budget_exhaustion_is_incomplete_and_persists_reservation(source_fixture, tmp_path):
    model = Judge()
    report = replay.replay_report(source_fixture[0], tmp_path / "budget", model, max_calls=1)
    assert len(model.payloads) == report["logical_calls_used"] == 1
    assert report["stop_reason"] == "model_call_budget"
    assert report["results"][0]["verdict"] == "harness_error"
    assert not report["summary"]["execution_complete"]


def test_budget_write_failure_latches_before_provider(source_fixture, tmp_path, monkeypatch):
    save = replay._save
    failed = False
    def fail_once(report, output):
        nonlocal failed
        if report["logical_calls_used"] and not failed:
            failed = True
            raise OSError("Do not persist arbitrary error text")
        save(report, output)
    monkeypatch.setattr(replay, "_save", fail_once)
    model = Judge()
    report = replay.replay_report(source_fixture[0], tmp_path / "write-failure", model)
    assert not model.payloads
    assert report["stop_reason"] == "budget_persistence_error"
    assert not report["summary"]["new_judge_usage"]["token_usage_complete"]
    assert "Do not persist arbitrary error text" not in json.dumps(report)


def test_interruption_preserves_reserved_call_without_implicit_resume(source_fixture, tmp_path):
    report = replay.replay_report(source_fixture[0], tmp_path / "interrupted", Judge(interrupt=True))
    assert report["stop_reason"] == "interrupted" and report["logical_calls_used"] == 1
    assert not report["summary"]["execution_complete"]
    with pytest.raises(ValueError, match="new output"):
        replay.replay_report(source_fixture[0], tmp_path / "interrupted", Judge())


@pytest.mark.parametrize("selected", [[("missing", 1, 0)], [("replay_fixture", 1, 0)] * 2, []])
def test_bad_selection_rejected(source_fixture, selected):
    with pytest.raises(ValueError, match="Selected turns"):
        replay.load_replay(source_fixture[0], selected_turns=selected)


def test_validate_cli_never_initializes_provider(source_fixture, monkeypatch, capsys):
    def forbidden():
        pytest.fail("Validation initialized provider")
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", forbidden)
    assert cli.main(["--input", str(source_fixture[0])]) == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    assert cli.turn_key("replay_fixture:2:1") == ("replay_fixture", 2, 1)


@pytest.mark.parametrize("change", ["answer", "trace_output"])
def test_normalization_drift_rejected_without_judge_calls(source_fixture, tmp_path, change):
    source, original, _ = source_fixture
    response = original["results"][0]["turns"][0]["response"]
    if change == "answer":
        response["answer"] = "来源：remote_limit_up_pool"
    else:
        response["tool_results"][0]["result"]["payload"] = {"different": "payload"}
    source.write_text(json.dumps(original), encoding="utf-8")
    before, model = source.read_bytes(), Judge()
    with pytest.raises(ValueError, match="normalization changed"):
        replay.replay_report(source, tmp_path / "normalized", model)
    assert not model.payloads and not (tmp_path / "normalized").exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("change", ["empty", "duplicate", "zero", "negative", "bool", "float"])
def test_invalid_manifest_cannot_create_empty_success(source_fixture, tmp_path, change):
    source, original, _ = source_fixture
    manifest = original["manifest"]
    if change == "empty":
        manifest["case_ids"], original["results"] = [], []
        manifest["dataset_hash"] = digest([])
    elif change == "duplicate":
        manifest["case_ids"] *= 2
        manifest["dataset_hash"] = digest([original["results"][0]["definition"]] * 2)
    else:
        manifest["trials"] = {"zero": 0, "negative": -1, "bool": True, "float": 2.0}[change]
    source.write_text(json.dumps(original), encoding="utf-8")
    model = Judge()
    with pytest.raises(ValueError):
        replay.replay_report(source, tmp_path / "invalid-manifest", model)
    assert not model.payloads and not (tmp_path / "invalid-manifest").exists()


@pytest.mark.parametrize("change", ["source", "input", "unreadable"])
def test_final_integrity_failure_is_durable_and_never_claims_complete(source_fixture, tmp_path, monkeypatch, change):
    source, _, _ = source_fixture
    save, snapshots, changed = replay._save, [], False
    def observe(report, output):
        nonlocal changed
        if report["results"] and report["results"][-1]["completed"] and not changed:
            changed = True
            if change == "source":
                source.write_bytes(source.read_bytes() + b"\n")
            elif change == "input":
                report["inputs"][0]["source_turn"]["response"]["answer"] = "Altered recorded answer"
            else:
                source.unlink()
        save(report, output)
        snapshots.append(json.loads((output / "report.json").read_text(encoding="utf-8")))
    monkeypatch.setattr(replay, "_save", observe)
    report = replay.replay_report(source, tmp_path / "integrity", Judge(),
                                  selected_turns=[("replay_fixture", 1, 0)])
    assert report["stop_reason"] == {"source": "source_changed", "input": "input_changed",
                                      "unreadable": "integrity_error"}[change]
    assert not report["integrity_verified"]
    assert report["summary"]["completed_turns"] == 1
    assert all(not snapshot["summary"]["execution_complete"] for snapshot in snapshots)
    assert snapshots[-1] == report
