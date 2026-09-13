"""Deterministic trajectory/terminal checks; unsupported semantics never pass."""

from typing import Literal

from pydantic import Field

from app.agent_eval.models import AssetRef, CaseSpec, Contract, EvaluationFinding, Profile
from app.agent_eval.recorder import canonical_json
from app.agents.react_runtime.catalog import arguments_model
from app.agents.react_runtime.contracts import VERSION
from app.agents.tools import TOOL_CONTRACT_VERSION, TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES
from app.models import AgentChatResponse


EVALUATOR_VERSION = "trajectory-terminal-v1"
SCHEMAS = {item.name: item for item in TOOL_SCHEMAS}


class CheckReport(Contract):
    evaluator_version: str = EVALUATOR_VERSION
    scope: Literal["trajectory_terminal"] = "trajectory_terminal"
    case: AssetRef
    verdict: Literal["pass", "fail", "needs_review"]
    # This partial evaluator is not a release gate or a complete Agent EvalResult.
    release_eligible: Literal[False] = False
    findings: list[EvaluationFinding] = Field(min_length=1)


def finding(key, verdict, detail):
    return EvaluationFinding(assertion_id=key, verdict=verdict, detail=detail)


def _attempts(response):
    executions = [t.output for t in response.tool_results if t.name == "react_execution"]
    if len(executions) != 1 or executions[0].get("version") != VERSION:
        raise ValueError("missing or incompatible final execution trace")
    execution = executions[0]
    if execution.get("tool_contract_version") != TOOL_CONTRACT_VERSION:
        raise ValueError("incompatible tool contract in execution trace")
    if execution.get("task_status") != response.task_status:
        raise ValueError("response status disagrees with execution trace")
    decisions = [t.output for t in response.tool_results if t.name == "react_decision"]
    errors = sum(t.name == "react_provider_error" for t in response.tool_results)
    if execution.get("model_calls") != len(decisions) + errors:
        raise ValueError("incomplete model decision trace")
    calls, ids = [], set()
    for decision in decisions:
        if not isinstance(decision.get("tool_calls"), list):
            raise ValueError("invalid decision tool_calls")
        for call in decision["tool_calls"]:
            if (not isinstance(call, dict) or not isinstance(call.get("name"), str)
                    or not isinstance(call.get("id"), str) or not isinstance(call.get("args"), dict)):
                raise ValueError("invalid native tool call")
            if call["id"] in ids:
                raise ValueError("ambiguous repeated call ID")
            ids.add(call["id"])
            calls.append(call)
    policies = {}
    for trace in response.tool_results:
        if trace.name != "react_policy":
            continue
        key, decision = trace.output.get("call_id"), trace.output.get("decision")
        if key not in ids or key in policies or decision not in {"allow", "reuse", "reject"}:
            raise ValueError("unpaired or ambiguous policy trace")
        policies[key] = decision
    if set(policies) != ids:
        raise ValueError("missing policy trace for attempted call")
    return calls, policies


def _trajectory(assertion, calls, policies, profile):
    target = assertion.target
    if assertion.kind == "argument_equals":
        tool, separator, parameter = target.partition(".")
        if not separator or not parameter:
            return "needs_review", "argument target must be tool.parameter"
    else:
        tool = target
    if tool not in SCHEMAS:
        return "needs_review", "unknown business tool in assertion"
    if assertion.kind != "tool_forbidden" and profile == "v1_close_review" and tool not in V1_CLOSED_MARKET_TOOL_NAMES:
        return "needs_review", "required tool is outside case profile"
    matches = [call for call in calls if call["name"] == tool]
    if assertion.kind == "tool_forbidden":
        return ("fail", "forbidden tool was attempted (including policy rejects)") if matches else ("pass", "no forbidden attempt")
    if assertion.kind == "tool_required":
        accepted = any(policies[call["id"]] in {"allow", "reuse"} for call in matches)
        return ("pass", "required tool accepted by policy; result quality checked separately") if accepted else ("fail", "required tool has no accepted call")
    model = arguments_model(SCHEMAS[tool])
    if parameter not in model.model_fields:
        return "needs_review", "unknown parameter in assertion"
    if not matches:
        return "fail", "no call available for required parameter check"
    for call in matches:
        try:
            values = model.model_validate(call["args"]).model_dump(mode="json")
        except ValueError:
            return "fail", "attempted arguments violate production schema"
        try:
            model.model_validate({**call["args"], parameter: assertion.expected})
        except ValueError:
            return "needs_review", "expected argument violates production schema"
        if canonical_json(values[parameter]) != canonical_json(assertion.expected):
            return "fail", "material argument differs from expected value"
    return "pass", "all attempted calls match material argument, with production defaults"


def evaluate_trajectory_terminal(case: CaseSpec, response: AgentChatResponse, *, profile: Profile) -> CheckReport:
    """Consume actual native decisions, not UI tool_calls or normalized tool inputs."""
    findings = []
    try:
        if response.generated_by != VERSION or profile != case.profile:
            raise ValueError("runtime version or execution profile mismatch")
        calls, policies = _attempts(response)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        findings.append(finding("$trace", "needs_review", str(error)))
        calls = policies = None
    for assertion in case.assertions:
        supported = assertion.evaluator == "trajectory" and assertion.kind in {
            "tool_required", "tool_forbidden", "argument_equals",
        }
        if not supported:
            verdict, detail = "needs_review", "assertion evaluator/kind is not implemented; no inferred answer correctness"
        elif calls is None:
            verdict, detail = "needs_review", "trace unavailable for deterministic scoring"
        else:
            try:
                verdict, detail = _trajectory(assertion, calls, policies, profile)
            except Exception as error:
                verdict, detail = "needs_review", "evaluator error: " + type(error).__name__
        findings.append(finding(assertion.id, verdict, detail))
    if calls is None or response.task_status is None:
        terminal = finding("$terminal", "needs_review", "no trusted terminal status")
    else:
        passed = response.task_status in case.expected_terminal.allowed_status
        terminal = finding("$terminal", "pass" if passed else "fail",
                           "terminal status matches case" if passed else "terminal status violates case (including false complete)")
    findings.append(terminal)
    accepted = [t.output for t in response.tool_results
                if t.name == "react_answer_check" and t.output.get("passed") is True]
    if (calls is not None and accepted and accepted[-1].get("status") == response.task_status
            and accepted[-1].get("missing") == [] and not case.expected_terminal.missing_requirement_ids):
        findings.append(finding("$missing", "pass", "both expected and delivered missing lists are empty"))
    else:
        findings.append(finding("$missing", "needs_review", "missing-text to user-requirement mapping needs semantic review"))
    verdict = "fail" if any(item.verdict == "fail" for item in findings) else (
        "needs_review" if any(item.verdict == "needs_review" for item in findings) else "pass")
    return CheckReport(case=AssetRef(id=case.case_id, version=case.case_version), verdict=verdict, findings=findings)
