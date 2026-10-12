"""Narrow types for result metadata bound to an actual current execution."""

from datetime import date

from pydantic import ValidationError

from app.agents.react_runtime.contracts import Compute
from evals.golden.judge_grounding import _same_json


DATE_TOOLS = {"limit_up_events", "market_event_pool", "first_board_ratings", "first_board_filter"}
USABLE_STATES = {"ok", "partial", "empty"}


def _current(record):
    return (isinstance(record, dict) and record.get("evidence_scope") == "current_run"
            and record.get("historical_reference") is False and type(record.get("result_state")) is str
            and record["result_state"] in USABLE_STATES)


def _iso_date(value):
    if type(value) is not str:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _matches(record, observation):
    return (_current(record) and isinstance(observation, dict) and observation.get("status") == "success"
            and record.get("tool") == observation.get("tool")
            and isinstance(record.get("arguments"), dict) and isinstance(observation.get("input"), dict)
            and _same_json(record["arguments"], observation["input"])
            and _same_json(record.get("payload"), observation.get("output")))


def _executed_result(reference, payload):
    path = reference["path"]
    if len(path) < 4:
        return None
    records = payload.get("synthetic_evidence", {})
    if path[0] == "synthetic_evidence" and path[2] == "payload":
        if not isinstance(records, dict) or not isinstance(payload.get("business_observations"), list):
            return None
        record = records[path[1]]
        if not _current(record):
            return None
        matches = [observed for observed in payload.get("business_observations", [])
                   if _matches(record, observed)]
        # An ambiguous duplicate record cannot supply an authoritative binding.
        if len(matches) != 1 or sum(_matches(candidate, matches[0]) for candidate in records.values()) != 1:
            return None
        observed = matches[0]
    elif path[0] == "business_observations" and path[2] == "output":
        observed = payload["business_observations"][path[1]]
        if not isinstance(observed, dict) or observed.get("status") != "success":
            return None
    else:
        return None
    output, arguments = observed.get("output"), observed.get("input")
    if (not isinstance(output, dict) or not isinstance(arguments, dict) or type(observed.get("tool")) is not str
            or output.get("execution_status") not in (None, "success", "completed")
            or output.get("result_state") not in (None, "ok", "partial", "empty")):
        return None
    return observed.get("tool"), arguments, output


def _computed_operation(reference, payload, active=frozenset()):
    path = reference["path"]
    if len(path) < 4 or path[0] != "synthetic_evidence" or path[2:4] != ["payload", "operation"]:
        return False
    records = payload.get("synthetic_evidence", {})
    if not isinstance(records, dict) or path[1] in active:
        return False
    record = records[path[1]]
    if not _current(record) or record.get("tool") != "compute_result":
        return False
    output = record.get("payload")
    operation = output.get("operation") if isinstance(output, dict) else None
    if (not isinstance(operation, dict) or not _same_json(operation, record.get("arguments"))
            or output.get("execution_status") not in (None, "success", "completed")
            or output.get("result_state") not in (None, "ok", "partial", "empty")):
        return False
    try:
        parsed = Compute.model_validate(operation).model_dump(mode="json")
    except ValidationError:
        return False
    if not _same_json(parsed, operation):  # Coerced or omitted values are not a recorded execution.
        return False
    parents = [operation["evidence_id"]]
    if operation["other_id"] is not None:
        parents.append(operation["other_id"])
    if (not _same_json(output.get("source_evidence_ids"), list(dict.fromkeys(parents)))
            or not isinstance(record.get("rows"), list) or not _same_json(record["rows"], output.get("items"))):
        return False
    for parent in parents:
        if not _current(records.get(parent)):
            return False
        parent_ref = {"path": ["synthetic_evidence", parent, "payload", "operation"]}
        if records[parent].get("tool") == "compute_result":
            if not _computed_operation(parent_ref, payload, active | {path[1]}):
                return False
        elif _executed_result(parent_ref, payload) is None:
            return False
    return True


def result_reference_capability(reference, payload):
    """Return a structural metadata role; never infer the asserted prose or truth."""
    path = reference["path"]
    if (len(path) < 4 or path[0] not in {"synthetic_evidence", "business_observations"}
            or path[2] != ("payload" if path[0] == "synthetic_evidence" else "output")):
        return None
    field = path[3]
    owner = payload[path[0]][path[1]]
    tool = owner.get("tool") if isinstance(owner, dict) else None
    # Same-named ordinary business fields retain their previous meaning. Only
    # these known result contracts can acquire the added query-metadata roles.
    known_field = (type(tool) is str and (field == "trade_date" and tool in DATE_TOOLS
        or field in {"window_days", "cache_status"} and tool == "stock_news"
        or field == "operation" and tool == "compute_result"))
    if not known_field:
        return None
    if field == "operation":
        return "computation_proof" if _computed_operation(reference, payload) else "unverified_query_status"
    if field not in {"trade_date", "window_days", "cache_status"}:
        return None
    observed = _executed_result(reference, payload)
    if observed is None or len(path) != 4:
        return "unverified_query_status"
    tool, arguments, output = observed
    if field == "trade_date":
        # Different dates must remain citable to diagnose a wrong-date response.
        if tool in DATE_TOOLS and _iso_date(output[field]) and _iso_date(arguments.get("trade_date")):
            return "response_date"
    elif tool == "stock_news" and isinstance(arguments.get("symbol"), str) and arguments["symbol"]:
        days = arguments.get("days")
        valid_window = type(days) is int and days > 0 and type(output.get("window_days")) is int and output["window_days"] == days
        if valid_window and field == "window_days":
            return "query_scope_proof"
        if valid_window and field == "cache_status" and type(output[field]) is str and output[field]:
            return "cache_status_proof"
    return "unverified_query_status"
