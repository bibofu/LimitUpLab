"""Asset safety and cross-reference contracts; no network, database or LLM."""

import json

import pytest
from pydantic import ValidationError

from app.agent_eval.loader import load_suite, world_digest
from app.agent_eval.models import BudgetSpec, CaseSpec, EvalResult, RunManifest, WorldSpec


def world_data():
    return {
        "world_id": "synthetic-contract-only", "world_version": 1,
        "profile": "v1_close_review", "anchor_datetime": "2026-09-13T18:00:00+08:00",
        "latest_local_trade_date": "2026-09-11",
        "trading_calendar": {
            "id": "test-calendar", "version": 1,
            "start_date": "2026-09-10", "end_date": "2026-09-13",
            "trading_dates": ["2026-09-10", "2026-09-11"],
        },
        "tool_contract_version": "agent-tools-v2", "evidence_version": "react-evidence-v4",
        "recordings": [{
            "id": "count", "tool": "market_summary", "arguments": {},
            "observation": {"state": "ok", "payload": {"count": 2}, "summary": "test only"},
            "origin": "synthetic", "provenance": "unit test, not real market data",
        }],
    }


def case_data():
    return {
        "case_id": "contract-only", "case_version": 1, "profile": "v1_close_review",
        "mode": "offline", "severity": "P0", "status": "candidate",
        "capabilities": ["count"], "world": {"id": "synthetic-contract-only", "version": 1},
        "conversation": [{"role": "user", "content": "查询涨停数量"}],
        "expected_requirements": [{
            "id": "count", "description": "report count", "source_turn": 0,
            "source_text": "涨停数量",
        }],
        "assertions": [{
            "id": "count-value", "evaluator": "fact", "kind": "number_equals",
            "requirement_id": "count", "target": "answer.count", "expected": 2,
        }],
        "expected_terminal": {"allowed_status": ["complete"]},
    }


def test_weekend_anchor_does_not_replace_latest_local_date():
    world = WorldSpec.model_validate(world_data())
    assert world.anchor_datetime.day == 13
    assert world.latest_local_trade_date.day == 11
    assert world_digest(world) == world_digest(WorldSpec.model_validate_json(world.model_dump_json()))
    changed = world_data()
    changed["recordings"][0]["observation"]["payload"]["count"] = 3
    assert world_digest(world) != world_digest(WorldSpec.model_validate(changed))


@pytest.mark.parametrize("field,value", [
    ("anchor_datetime", "2026-09-13T18:00:00"),
    ("anchor_datetime", "2026-09-14T18:00:00+08:00"),
    ("latest_local_trade_date", "2026-09-13"),
    ("profile", "production"),
    ("world_version", 0),
    ("unexpected", True),
])
def test_world_rejects_invalid_time_profile_and_unknown_fields(field, value):
    data = world_data()
    data[field] = value
    with pytest.raises(ValidationError):
        WorldSpec.model_validate(data)


@pytest.mark.parametrize("dates", [
    ["2026-09-11", "2026-09-10"],
    ["2026-09-11", "2026-09-11"],
    ["2026-09-09", "2026-09-11"],
])
def test_calendar_rejects_unsorted_duplicate_and_out_of_range_days(dates):
    data = world_data()
    data["trading_calendar"]["trading_dates"] = dates
    with pytest.raises(ValidationError):
        WorldSpec.model_validate(data)


@pytest.mark.parametrize("change", ["source", "turn", "reference", "duplicate", "world"])
def test_case_rejects_broken_requirement_and_world_references(change):
    data = case_data()
    if change == "source":
        data["expected_requirements"][0]["source_text"] = "not requested"
    elif change == "turn":
        data["expected_requirements"][0]["source_turn"] = 3
    elif change == "reference":
        data["assertions"][0]["requirement_id"] = "invented"
    elif change == "duplicate":
        data["expected_requirements"] *= 2
    else:
        data["world"] = None
    with pytest.raises(ValidationError):
        CaseSpec.model_validate(data)


def test_live_current_requires_observation_based_facts():
    data = case_data()
    data.update(mode="live_current", world=None)
    with pytest.raises(ValidationError):
        CaseSpec.model_validate(data)
    data["assertions"][0].update(kind="answer_matches_observation", expected=None)
    assert CaseSpec.model_validate(data).world is None


def test_suite_checks_missing_world_duplicate_identity_and_profile(tmp_path):
    case_path, world_path = tmp_path / "case.json", tmp_path / "world.json"
    case_path.write_text(json.dumps(case_data()), encoding="utf-8")
    world_path.write_text(json.dumps(world_data()), encoding="utf-8")
    assert len(load_suite([case_path], [world_path])[0]) == 1
    for cases, worlds in [([case_path], []), ([case_path, case_path], [world_path]),
                          ([case_path], [world_path, world_path])]:
        with pytest.raises(ValueError):
            load_suite(cases, worlds)
    changed = world_data()
    changed["profile"] = "extended"
    world_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="profile mismatch"):
        load_suite([case_path], [world_path])


@pytest.mark.parametrize("findings", [[], [{
    "assertion_id": "fact", "verdict": "needs_review", "detail": "unparsed units",
}], [{"assertion_id": "fact", "verdict": "fail", "detail": "wrong date"}]])
def test_abstention_or_empty_checks_cannot_be_pass(findings):
    with pytest.raises(ValidationError):
        EvalResult.model_validate({
            "case": {"id": "test", "version": 1}, "run_id": "run",
            "verdict": "pass", "findings": findings, "model_calls": 1,
            "total_tokens": None, "elapsed_seconds": 1,
        })


def test_budget_requires_explicit_positive_limits():
    data = dict(max_agent_runs=1, max_model_calls=8, max_input_tokens=100,
                max_output_tokens=100, max_estimated_cost_usd=1, max_wall_time_seconds=30)
    assert BudgetSpec.model_validate(data).max_model_calls == 8
    for field in data:
        with pytest.raises(ValidationError):
            BudgetSpec.model_validate({**data, field: None})
        with pytest.raises(ValidationError):
            BudgetSpec.model_validate({**data, field: 0})


@pytest.mark.parametrize("model", [CaseSpec, WorldSpec, EvalResult, RunManifest])
def test_contracts_export_closed_json_schema(model):
    assert model.model_json_schema()["additionalProperties"] is False
