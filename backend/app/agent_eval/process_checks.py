"""Trace-order process diagnostics, independent of final answer extraction."""

from app.agent_eval.evaluators import _attempts, finding
from app.agent_eval.models import AssetRef, Contract, EvaluationFinding
from app.agent_eval.recorder import canonical_json
from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import CURRENT_SCOPE, EvidenceStore, EVIDENCE_VERSION
from typing import Literal
from pydantic import Field


class ProcessReport(Contract):
    evaluator_version: Literal["process-diagnostic-v1"] = "process-diagnostic-v1"
    scope: Literal["dependencies_select_and_visible_delivery"] = "dependencies_select_and_visible_delivery"
    case: AssetRef
    verdict: Literal["pass", "fail", "needs_review"]
    release_eligible: Literal[False] = False
    findings: list[EvaluationFinding]
    metrics: dict[str, int] = Field(default_factory=dict)


def evaluate_process(case, response):
    """No mandatory read/compute route. Duplicate requests are metrics, not failures."""
    findings, metrics = [], {"tool_attempts": 0, "repeated_signatures": 0,
                            "read_attempts": 0, "compute_attempts": 0}
    try:
        if response.generated_by != VERSION:
            raise ValueError("incompatible runtime")
        calls, policies = _attempts(response)
        metrics["tool_attempts"] = len(calls)
        signatures = [canonical_json([c["name"], c["args"]]) for c in calls]
        metrics["repeated_signatures"] = len(signatures) - len(set(signatures))
        execution = next(t.output for t in response.tool_results if t.name == "react_execution")
        records = execution["evidence"]
        available, visible, pending, observed = set(), set(), {}, set()
        computed = 0
        for trace in response.tool_results:
            if trace.name == "react_decision":
                # All calls in one model decision see the same prior observations.
                for call in trace.output["tool_calls"]:
                    pending[call["id"]] = call
                    if call["name"] not in {"read_evidence", "compute_result"}:
                        continue
                    metrics["read_attempts" if call["name"] == "read_evidence" else "compute_attempts"] += 1
                    ids = [call["args"].get("evidence_id")]
                    if call["args"].get("other_id"):
                        ids.append(call["args"]["other_id"])
                    valid = all(key in available for key in ids)
                    findings.append(finding("dependency:" + call["id"], "pass" if valid else "fail",
                        "source observed before decision" if valid else "source not observed before this decision"))
            elif trace.name == "react_observe":
                for view in trace.output["results"]:
                    call_id = view.get("call_id")
                    if call_id not in pending or call_id in observed:
                        raise ValueError("observation missing preceding call or duplicated")
                    observed.add(call_id)
                    key = view.get("evidence_id")
                    if not key:
                        continue
                    record = records.get(key)
                    if record is None:
                        raise ValueError("observed evidence absent from final records")
                    if record.get("evidence_id") != key or record.get("schema_version") != EVIDENCE_VERSION:
                        raise ValueError("incompatible evidence identity/version")
                    if (record.get("evidence_scope") != CURRENT_SCOPE or record.get("historical_reference")
                            or view.get("evidence_scope") != CURRENT_SCOPE or view.get("historical_reference")):
                        continue
                    available.add(key)
                    if view.get("result_state") not in {"ok", "partial"}:
                        continue
                    store = EvidenceStore()
                    replay_id = store.add(tool=record["tool"], payload=record["payload"],
                        state=record["result_state"], arguments=record["arguments"], sources=record.get("sources"))
                    replay = store.view(replay_id, offset=view["offset"], limit=max(1, len(view["rows"])))
                    if any(replay[f] != view.get(f) for f in replay.keys() - {"evidence_id", "retrieved_at"}):
                        raise ValueError("visible view cannot be reproduced from full evidence")
                    rows = view.get("rows", [])
                    # Visible identities are bound to the row's own date, not answer date.
                    visible.update((r.get("trade_date"), r.get("symbol"), r.get("name"))
                                   for r in rows if isinstance(r, dict))
                    call = pending[call_id]
                    if call["name"] != "compute_result" or policies[call_id] not in {"allow", "reuse"}:
                        continue
                    args = record.get("arguments", {})
                    source = records.get(args.get("evidence_id"), {})
                    source_rows = source.get("payload", {}).get("events")
                    if (args.get("operation") != "select" or args.get("filters") or source_rows is None
                            or args.get("other_id") or args.get("sort_by") not in {None, "amount"}):
                        findings.append(finding("compute:" + call_id, "needs_review", "compute operation outside independent adapter"))
                        continue
                    if any(args.get(k) != value for k, value in call["args"].items()):
                        raise ValueError("computed record arguments disagree with model call")
                    ordered = list(source_rows)
                    if args.get("sort_by") == "amount":
                        if any(type(r.get("amount")) not in (int, float) for r in ordered):
                            raise ValueError("amount sort has missing/incompatible values")
                        ordered.sort(key=lambda r: r["amount"], reverse=args.get("descending", True))
                    offset, limit = args.get("offset", 0), args.get("limit", 20)
                    if type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1:
                        raise ValueError("unsupported select window")
                    expected = ordered[offset:offset + limit]
                    payload = record.get("payload", {})
                    actual = payload.get("items")
                    if actual is None:
                        raise ValueError("computed select payload missing items")
                    computed += 1
                    findings.append(finding("compute:" + call_id, "pass" if actual == expected else "needs_review",
                        "independent select replay matches full result" if actual == expected
                        else "runtime computation/trace differs from independent replay; not an answer failure"))
        if set(pending) != observed:
            raise ValueError("missing terminal observations")
        for assertion in case.assertions:
            if assertion.target != "answer.ordered_events":
                continue
            expected = assertion.expected
            required = {(expected["trade_date"], r["symbol"], r["name"]) for r in expected["ordered_members"]}
            findings.append(finding("visible:" + assertion.id,
                "pass" if required and required <= visible else "needs_review",
                "required identities were visible before final delivery" if required and required <= visible
                else "required list visibility not established; inspect unsupported routes or missing evidence"))
        metrics["independent_select_replays"] = computed
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration) as error:
        findings.append(finding("$process_trace", "needs_review", str(error)))
    if not findings:
        findings.append(finding("$process_scope", "needs_review", "no process checks applicable"))
    verdict = "needs_review" if any(f.verdict == "needs_review" for f in findings) else (
        "fail" if any(f.verdict == "fail" for f in findings) else "pass")
    return ProcessReport(case=AssetRef(id=case.case_id, version=case.case_version),
                         verdict=verdict, findings=findings, metrics=metrics)
