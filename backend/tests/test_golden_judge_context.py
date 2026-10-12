"""Recorded execution context is auditable, scoped, and never an answer oracle."""

from copy import deepcopy
import hashlib
import json

import pytest

from evals.golden import judge_context as context
from evals.golden import runtime_context as runtime
from evals.golden.reporting import digest
from test_golden_judge_replay import Judge, source_fixture


def turn(index=0, session="main", prepared=0, effective=0, calls=()):
    business = [{"name": name, "status": "success", "input": {}, "output": {}} for name in calls]
    execution = {"version": "react-runtime-v34", "tool_calls": len(calls), "evidence": {},
                 "context_message_count": effective, "task_status": "complete", "stop_reason": "answered"}
    return {"index": index, "user": "Recorded user request", "session": session,
        "memory_before": None, "context_message_count": prepared, "runtime_metadata": [],
        "response": {"session_id": "session-" + session, "run_id": "run-" + str(index),
            "answer": "Recorded answer", "task_status": "complete", "stop_reason": "answered",
            "tool_calls": list(calls), "tool_results": [*business,
                {"name": "react_execution", "output": execution},
                {"name": "react_task_context", "output": {"current_task": "Agent interpretation"}}]}}


def binding():
    return {"verified": True, "source_sha256": runtime.REVIEWED_WORLD_SHA256}


def capability(observations, path):
    value = observations
    for key in path:
        value = value[key]
    return context.evaluator_reference_capability(
        {"path": ["evaluator_observations", *path], "value": value}, {"evaluator_observations": observations})


def test_zero_is_recorded_execution_only_and_does_not_mean_no_data():
    source = turn(prepared=2, effective=2)
    source["response"]["tool_results"][0]["output"]["evidence"] = {"previous": {"rows": [1]}}
    before = deepcopy(source)
    derived = context.build_judge_context(source)
    observed = derived["observations"]
    assert observed["execution"]["business_tool_call_count"] == 0
    assert observed["execution"]["evidence_record_count"] == 1
    assert capability(observed, ["execution", "business_tool_call_count"]) == "query_status_proof"
    assert "market_count" not in json.dumps(observed) and "no_data" not in json.dumps(observed)
    assert source == before and derived["provenance"]["source_sha256"]


@pytest.mark.parametrize("change", ["missing_execution", "missing_calls", "missing_run", "unknown_version",
                                    "bool_count", "mismatched_count", "unrecorded_business", "duplicate_execution"])
def test_missing_or_inconsistent_execution_cannot_prove_zero(change):
    source = turn()
    response, execution = source["response"], source["response"]["tool_results"][0]["output"]
    if change == "missing_execution":
        response["tool_results"] = []
    elif change == "missing_calls":
        del response["tool_calls"]
    elif change == "missing_run":
        del response["run_id"]
    elif change == "unknown_version":
        execution["version"] = "unreviewed-runtime"
    elif change == "bool_count":
        execution["tool_calls"] = False
    elif change == "mismatched_count":
        execution["tool_calls"] = 1
    elif change == "unrecorded_business":
        response["tool_results"].append({"name": "stock_news", "input": {}, "output": {}})
    else:
        response["tool_results"].append(deepcopy(response["tool_results"][0]))
    derived = context.build_judge_context(source)
    assert derived["observations"]["execution"]["business_tool_call_count"] is None
    assert derived["diagnostics"] == ["business_tool_count_unverified"]


def test_session_counts_do_not_copy_other_session_text_or_memory():
    prior = turn()
    prior["user"] = "OTHER_SESSION_SECRET"
    source = turn(index=1, session="other")
    derived = context.build_judge_context(source, prior_turns=[prior])
    assert "OTHER_SESSION_SECRET" not in json.dumps(derived)
    observed = derived["observations"]
    assert observed["task"]["recorded_prior_user_requests"] == []
    assert observed["session"]["prepared_message_count"] == 0
    assert observed["session"]["memory_record_present"] is False
    del source["memory_before"]
    del source["context_message_count"]
    session = context.build_judge_context(source)["observations"]["session"]
    assert session["memory_record_present"] is None and session["prepared_message_count"] is None


def test_prepared_history_and_runtime_filtered_history_are_distinct():
    previous = turn()
    previous["user"] = "Find stocks common to both dates"
    source = turn(index=1, prepared=2, effective=0)
    source["memory_before"] = {"session_id": "session-main", "summary": "MEMORY_BODY_SECRET"}
    observed = context.build_judge_context(source, prior_turns=[previous])["observations"]
    assert observed["session"]["prepared_message_count"] == 2
    assert observed["session"]["runtime_history_message_count"] == 0
    assert observed["session"]["memory_record_present"] is True
    assert "MEMORY_BODY_SECRET" not in json.dumps(observed)
    assert observed["task"]["recorded_prior_user_requests"][0]["user"] == previous["user"]
    assert "not_proof_of_current_model_input" in observed["task"]["prior_requests_scope"]
    assert capability(observed, ["task", "recorded_prior_user_requests", 0, "user"]) == "evaluation_context"
    assert capability(observed, ["task", "agent_interpretation"]) == "evaluation_context"
    assert capability(observed, ["session", "session_sha256"]) == "unrecognized_evidence"


def test_context_does_not_derive_from_oracle_or_old_judgement():
    source = turn()
    plain = context.build_judge_context(source)
    source.update(expected={"rows": [["ORACLE_SECRET"]]}, judgements=[{"reason": "ORACLE_SECRET"}], verdict="fail")
    assert context.build_judge_context(source) == plain
    source["response"]["answer"] = "Changed recorded answer"
    assert context.build_judge_context(source)["provenance"]["source_sha256"] != plain["provenance"]["source_sha256"]


def test_count_semantics_require_reviewed_fixture_binding():
    source = turn(calls=["market_event_pool"])
    trace = source["response"]["tool_results"][0]
    trace["input"] = {"event_type": "limit_up", "result_mode": "count", "market": None, "query": None}
    trace["output"] = {"event_type": "limit_up", "result_mode": "count", "items": [], "matched_count": 8,
                       "synthetic": True, "source": "synthetic-golden-world-v1"}
    assert context.build_judge_context(source)["observations"]["tool_semantics"] == []
    observed = context.build_judge_context(source, fixture_binding=binding())["observations"]
    contract = observed["tool_semantics"][0]["contract"]
    assert contract == {"event_status": "closed", "count_basis": "matching_fixture_event_rows",
                        "items_omitted_by_count_mode": True, "market_scope": "all_fixture_market_segments"}
    assert capability(observed, ["tool_semantics", 0, "contract", "event_status"]) == "query_scope_proof"
    trace["input"]["query"] = "Specific stock"
    narrowed = context.build_judge_context(source, fixture_binding=binding())["observations"]["tool_semantics"]
    assert "market_scope" not in narrowed[0]["contract"]
    assert context.build_judge_context(source, fixture_binding={**binding(), "source_sha256": "new"})["observations"]["tool_semantics"] == []


def test_news_fixture_cannot_certify_external_window_completeness():
    source = turn(calls=["stock_news"])
    source["response"]["tool_results"][0]["output"] = {"items": [{"title": "Synthetic"}], "data_missing": [],
        "window_days": 7, "synthetic": True, "source": "synthetic-golden-world-v1"}
    observed = context.build_judge_context(source, fixture_binding=binding())["observations"]
    assert observed["tool_semantics"][0]["contract"] == {"coverage_scope": "synthetic_fixture_only",
        "external_news_completeness": "not_established", "returned_items_count": 1}


@pytest.mark.parametrize("malformed", [None, [], {"tool": []}, {"tool": "stock_news", "fixture_source_sha256": "forged"}])
def test_malformed_semantics_fail_closed(malformed):
    observed = context.build_judge_context(turn())["observations"]
    observed["tool_semantics"] = [malformed]
    assert context.evaluator_reference_capability({"path": ["evaluator_observations", "tool_semantics", 0,
        "contract", "returned_items_count"], "value": 0}, {"evaluator_observations": observed}) == "unrecognized_evidence"


def test_historical_binding_verifies_complete_evaluator_tree_before_world(monkeypatch):
    sources = {runtime.WORLD_PATH: b"reviewed fixture", "backend/evals/golden/judge.py": b"historical judge"}
    hashes = {key: hashlib.sha256(value).hexdigest() for key, value in sources.items()}
    monkeypatch.setattr(runtime, "_revision_sources", lambda _: sources)
    monkeypatch.setattr(runtime, "REVIEWED_WORLD_SHA256", hashes[runtime.WORLD_PATH])
    manifest = {"git_commit": "a" * 40, "evaluator_hash": digest(hashes)}
    assert runtime.historical_fixture_binding(manifest)["verified"]
    sources["backend/evals/golden/judge.py"] = b"changed judge, same world"
    assert not runtime.historical_fixture_binding(manifest)["verified"]
    assert not runtime.historical_fixture_binding({**manifest, "git_commit": "missing"})["verified"]


def test_historical_missing_git_and_unknown_world_do_not_use_current_fixture(monkeypatch):
    def missing(_):
        raise OSError("missing historical source")
    monkeypatch.setattr(runtime, "_revision_sources", missing)
    assert not runtime.historical_fixture_binding({"git_commit": "a" * 40})["verified"]
    sources = {runtime.WORLD_PATH: b"unknown fixture"}
    monkeypatch.setattr(runtime, "_revision_sources", lambda _: sources)
    manifest = {"git_commit": "a" * 40,
                "evaluator_hash": digest({key: hashlib.sha256(value).hexdigest() for key, value in sources.items()})}
    assert not runtime.historical_fixture_binding(manifest)["verified"]


def test_live_context_and_fixed_answer_replay_use_identical_derivation(tmp_path, monkeypatch):
    from evals.golden import replay, runner
    from test_golden_runner import ScriptedModel, example_case

    case, model = example_case(), ScriptedModel()
    result = runner.run_case(case, trial=1, directory=tmp_path / "agent", provider=model, judge_provider=model)
    assert result["verdict"] == "pass", result["turns"][0]["checks"]
    recorded = result["turns"][0]
    source = tmp_path / "source.json"
    original = {"mode": "live-model-frozen-tools", "manifest": {"case_ids": [case.id], "trials": 1,
        "dataset_hash": digest([case.model_dump(mode="json")])}, "results": [result]}
    source.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    before = source.read_bytes()
    monkeypatch.setattr(replay, "historical_fixture_binding", lambda _: binding())
    loaded = replay.load_replay(source, cases=[case])
    item = loaded["inputs"][0]
    assert item["derived_context"]["observations"] == recorded["evaluator_context"]["observations"]
    assert item["derived_context"]["provenance"]["source_sha256"] == recorded["evaluator_context"]["provenance"]["source_sha256"]
    # This is the original v1 fixed-input algorithm, independently reconstructed.
    fixed = {key: recorded[key] for key in ("user", "expected", "response", "events", "runtime_metadata")}
    fixed["drafts"] = replay.visible_drafts(recorded["events"])
    assert item["input_sha256"] == digest(fixed)
    assert item["source_turn"] == recorded and source.read_bytes() == before
    assert loaded["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert loaded["derived_inputs_sha256"] != loaded["fixed_inputs_sha256"]


@pytest.mark.parametrize("change", ["observations", "source_context", "original_context"])
def test_derived_context_integrity_failure_is_persisted(source_fixture, tmp_path, monkeypatch, change):
    from evals.golden import replay

    source, _, _ = source_fixture
    before, save, changed = source.read_bytes(), replay._save, False
    def mutate_after_judging(report, output):
        nonlocal changed
        if report["results"] and report["results"][-1]["completed"] and not changed:
            changed = True
            item = report["inputs"][0]
            if change == "observations":
                item["derived_context"]["observations"]["execution"]["business_tool_call_count"] = 100
            elif change == "source_context":
                item["source_turn"]["context_message_count"] = 100
            else:
                item["source_turn"]["evaluator_context"] = {"changed": True}
        save(report, output)
    monkeypatch.setattr(replay, "_save", mutate_after_judging)
    report = replay.replay_report(source, tmp_path / "replay", Judge(),
                                  selected_turns=[("replay_fixture", 1, 0)])
    assert report["stop_reason"] == "derived_context_changed"
    assert not report["summary"]["execution_complete"] and not report["integrity_verified"]
    assert json.loads((tmp_path / "replay/report.json").read_text(encoding="utf-8")) == report
    assert source.read_bytes() == before
