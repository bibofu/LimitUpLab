"""Derive bounded Judge context from recorded execution, without oracle labels."""

from copy import deepcopy

from app.models import AGENT_CONTROL_TRACE_NAMES
from evals.golden.reporting import digest
from evals.golden.runtime_context import REVIEWED_WORLD_SHA256, fixture_semantics

CONTEXT_VERSION = "golden-evaluator-observations-v1"
RECORDED_FIELDS = ("index", "user", "session", "response", "runtime_metadata", "context_message_count", "memory_before")


def _natural(value):
    return type(value) is int and value >= 0


def context_sources(turn, prior_turns):
    """Keep missing distinct from null; retain only prior requests in this session."""
    response = turn.get("response") or {}
    session = response.get("session_id")
    prior = []
    for item in prior_turns:
        old_response = item.get("response") or {}
        if (session and old_response.get("session_id") == session and _natural(item.get("index"))
                and _natural(turn.get("index")) and item["index"] < turn["index"]
                and isinstance(item.get("user"), str)):
            prior.append({"index": item["index"], "user": item["user"], "session_id": session})
    return {"turn": {key: deepcopy(turn[key]) for key in RECORDED_FIELDS if key in turn}, "prior_requests": prior}


def build_judge_context(turn, *, prior_turns=(), fixture_binding=None):
    sources = context_sources(turn, prior_turns)
    snapshot = sources["turn"]
    response = snapshot.get("response") or {}
    traces = response.get("tool_results", [])
    traces = traces if isinstance(traces, list) else []
    executions = [t.get("output") for t in traces if isinstance(t, dict) and t.get("name") == "react_execution"]
    execution = executions[0] if len(executions) == 1 and isinstance(executions[0], dict) else {}
    calls = response.get("tool_calls")
    business = [t for t in traces if isinstance(t, dict) and isinstance(t.get("name"), str)
                and not t["name"].startswith("react_") and t["name"] not in AGENT_CONTROL_TRACE_NAMES]
    valid_execution = (execution.get("version") in {"react-runtime-v33", "react-runtime-v34"}
        and isinstance(response.get("run_id"), str) and bool(response["run_id"])
        and response.get("task_status") in {"complete", "partial", "empty", "clarify", "refuse", "error", "cancelled"}
        and isinstance(response.get("stop_reason"), str) and bool(response["stop_reason"])
        and execution.get("task_status") == response.get("task_status")
        and execution.get("stop_reason") == response.get("stop_reason"))
    count_known = (valid_execution and _natural(execution.get("tool_calls")) and isinstance(calls, list)
        and all(isinstance(name, str) for name in calls)
        and execution["tool_calls"] == len(calls) == len(business)
        and [t["name"] for t in business] == calls)
    session_id = response.get("session_id")
    session = {"session_sha256": digest(session_id) if isinstance(session_id, str) else None,
        "prepared_message_count": snapshot.get("context_message_count") if _natural(snapshot.get("context_message_count")) else None,
        "runtime_history_message_count": execution.get("context_message_count")
            if valid_execution and _natural(execution.get("context_message_count")) else None,
        "memory_record_present": None}
    if "memory_before" in snapshot:
        memory = snapshot["memory_before"]
        if memory is None:
            session["memory_record_present"] = False
        elif isinstance(memory, dict) and memory.get("session_id") == session_id:
            session["memory_record_present"] = True
    tasks = [t.get("output") for t in traces if isinstance(t, dict) and t.get("name") == "react_task_context"]
    task = {"recorded_prior_user_requests": [{"user": item["user"], "turn_index": item["index"],
             "session_sha256": session["session_sha256"]} for item in sources["prior_requests"]],
            "prior_requests_scope": "recorded_same_session_requests_not_proof_of_current_model_input"}
    if len(tasks) == 1 and isinstance(tasks[0], dict) and isinstance(tasks[0].get("current_task"), str):
        task["agent_interpretation"] = tasks[0]["current_task"]
    binding = fixture_binding or {"verified": False, "source_sha256": None}
    observations = {"version": CONTEXT_VERSION,
        "execution": {"business_tool_call_count": execution["tool_calls"] if count_known else None,
            "evidence_record_count": len(execution["evidence"]) if valid_execution and isinstance(execution.get("evidence"), dict) else None,
            "task_status": response.get("task_status") if valid_execution else None,
            "stop_reason": response.get("stop_reason") if valid_execution else None},
        "session": session, "task": task, "tool_semantics": fixture_semantics(business, binding)}
    return {"observations": observations, "provenance": {"version": CONTEXT_VERSION,
        "source_fields": list(sources["turn"]), "source_sha256": digest(sources),
        "observations_sha256": digest(observations), "fixture_binding": deepcopy(binding)},
        "diagnostics": [] if count_known else ["business_tool_count_unverified"]}


def evaluator_reference_capability(reference, payload):
    path = reference.get("path", [])
    if not path or path[0] != "evaluator_observations":
        return None
    observed = payload.get("evaluator_observations")
    if not isinstance(observed, dict) or observed.get("version") != CONTEXT_VERSION:
        return "unrecognized_evidence"
    try:
        actual = observed
        for key in path[1:]:
            actual = actual[key]
        if actual is None or digest(actual) != digest(reference.get("value")):
            return "unrecognized_evidence"
    except (KeyError, IndexError, TypeError):
        return "unrecognized_evidence"
    if len(path) == 3 and path[1] == "execution":
        if path[2] in {"business_tool_call_count", "evidence_record_count"} and _natural(actual):
            return "query_status_proof"
        if path[2] in {"task_status", "stop_reason"} and isinstance(actual, str):
            return "query_status_proof"
    if len(path) == 3 and path[1] == "session":
        if path[2] in {"prepared_message_count", "runtime_history_message_count"} and _natural(actual):
            return "query_status_proof"
        if path[2] == "memory_record_present" and type(actual) is bool:
            return "query_status_proof"
    if len(path) == 5 and path[1] == "tool_semantics" and type(path[2]) is int and path[3] == "contract":
        row = observed["tool_semantics"][path[2]]
        allowed = {"market_event_pool": {"event_status", "count_basis", "items_omitted_by_count_mode", "market_scope"},
                   "stock_news": {"coverage_scope", "external_news_completeness", "returned_items_count"}}
        valid_values = {"event_status": "closed", "count_basis": "matching_fixture_event_rows",
            "items_omitted_by_count_mode": True, "market_scope": "all_fixture_market_segments",
            "coverage_scope": "synthetic_fixture_only", "external_news_completeness": "not_established"}
        value_valid = (_natural(actual) if path[4] == "returned_items_count"
                       else path[4] in valid_values and digest(actual) == digest(valid_values[path[4]]))
        if (path[2] >= 0 and isinstance(row, dict) and isinstance(row.get("tool"), str)
                and row.get("fixture_source_sha256") == REVIEWED_WORLD_SHA256
                and path[4] in allowed.get(row["tool"], set()) and value_valid):
            return "query_scope_proof"
    if len(path) >= 3 and path[1] == "task":
        return "evaluation_context"
    return "unrecognized_evidence"
