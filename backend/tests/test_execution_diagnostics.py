"""Input failures and durable budget failures expose locations without private exception data."""

import json
from types import SimpleNamespace

import pytest

from app.agents.react_runtime import runtime
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatRequest
from app.services.execution_diagnostics import exception_diagnostics
from evals.golden import calibration, runner
from evals.golden.contracts import Case, Expectation, Turn


def denied():
    error = PermissionError(13, "PRIVATE_EXCEPTION_BODY", "C:/PRIVATE_PATH/key.env")
    error.winerror = 5
    return error


class Provider:
    def __init__(self):
        self.calls = 0

    def generate_messages(self, *args, **kwargs):
        self.calls += 1
        raise denied()


def case():
    return Case(id="budget_persistence_fixture", category="single", family="protocol", tags=["protocol"],
                turns=[Turn(user="查询合成资料", expect=Expectation())])


@pytest.mark.parametrize("stage", ["context", "provider"])
def test_input_failure_locations_distinguish_context_from_provider_without_leaking(stage, monkeypatch):
    underlying = Provider()
    provider = runner.BudgetedProvider(underlying, runner.Budget(5))
    if stage == "context":
        def fail(*args, **kwargs):
            raise denied()
        monkeypatch.setattr(runtime, "prepare_task_context", fail)
    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    result = runtime.run(AgentChatRequest(session_id="diagnostic", message="PRIVATE_REQUEST"), registry, provider)
    trace = next(item for item in result.tool_results if item.name == "react_input_security")
    diagnostic = trace.output["diagnostics"]
    assert result.stop_reason == "input_policy_error" and result.tool_calls == []
    assert diagnostic["stage"] == ("input_context" if stage == "context" else "input_review")
    assert diagnostic["errno"] == 13 and diagnostic["winerror"] == 5
    assert underlying.calls == (stage == "provider")
    if stage == "provider":
        assert diagnostic["origin"] == "provider_invocation" and diagnostic["provider_entered"] is True
        assert "evals.golden.runner.BudgetedProvider._invoke" in diagnostic["locations"]
    else:
        assert "provider_entered" not in diagnostic
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_failed_reservation_keeps_conservative_count_and_never_retries_or_enters_provider():
    writes = []
    def reserve(count):
        writes.append(count)  # A partial durable write cannot safely be rolled back.
        raise denied()
    budget = runner.Budget(5, on_take=reserve)
    underlying = Provider()
    provider = runner.BudgetedProvider(underlying, budget)
    for _ in range(2):
        with pytest.raises(runner.BudgetPersistenceError):
            provider.generate_messages()
    assert writes == [1] and budget.used == 1 and underlying.calls == 0
    diagnostic = budget.persistence_diagnostics
    assert diagnostic["stage"] == "budget_reservation" and diagnostic["origin"] == "budget_persistence"
    assert diagnostic["provider_entered"] is False and diagnostic["errno"] == 13
    assert diagnostic["exception_types"] == ["BudgetPersistenceError", "PermissionError"]
    assert "evals.golden.runner.Budget.take" in diagnostic["locations"]
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_custom_exception_names_markers_and_stack_frames_are_not_serialized():
    custom = type("PRIVATE_ERROR_NAME", (Exception,), {})("PRIVATE_MESSAGE")
    custom.execution_origin = {"origin": "PRIVATE_ORIGIN", "provider_entered": "PRIVATE_VALUE"}
    try:
        raise custom
    except Exception as error:
        diagnostic = exception_diagnostics(error, stage="PRIVATE_STAGE")
    assert diagnostic == {"stage": "unknown", "exception_types": ["OtherError"], "locations": []}


def test_persistence_failure_is_harness_error_and_skips_judge(tmp_path):
    def reserve(count):
        raise denied()
    budget = runner.Budget(10, on_take=reserve)
    underlying, judge = Provider(), Provider()
    result = runner.run_case(case(), trial=1, directory=tmp_path,
        provider=runner.BudgetedProvider(underlying, budget), judge_provider=judge)
    assert result["verdict"] == result["turns"][0]["verdict"] == "harness_error"
    assert result["stop_reason"] == "budget_persistence_error" and result["completed"] is False
    assert len(result["budget_persistence_diagnostics"]) == 1
    assert underlying.calls == judge.calls == 0 and budget.used == 1
    assert "PRIVATE" not in json.dumps(result)


def test_calibration_stops_after_reservation_failure_without_fake_provider_calls(monkeypatch):
    original_budget = runner.Budget
    reservations = []
    def failing_budget(maximum, on_take):
        def reserve(count):
            reservations.append(count)
            on_take(count)
            raise denied()
        return original_budget(maximum, on_take=reserve)
    monkeypatch.setattr(runner, "Budget", failing_budget)
    provider = Provider()
    report = calibration.run_calibration(provider, cases=calibration.load_calibration_cases()[:2])
    assert report["stop_reason"] == "budget_persistence_error"
    assert report["summary"]["counts"] == {"harness_error": 1}
    assert len(report["results"]) == 1 and not report["summary"]["complete"]
    assert report["summary"]["judge_phase_call_count"] == 0
    assert reservations == [1] and provider.calls == 0 and report["logical_calls_used"] == 1
    assert "PRIVATE" not in json.dumps(report)
