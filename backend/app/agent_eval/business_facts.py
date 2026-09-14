"""Reviewed business requirements: ties, reclosure, historical counts and valid empty."""

from typing import Literal

from pydantic import Field

from app.agent_eval.event_facts import ListExtraction
from app.agent_eval.evaluators import _attempts, finding
from app.agent_eval.models import AssetRef, Contract, EvaluationFinding
from app.agent_eval.recorder import digest
from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import CURRENT_SCOPE, EVIDENCE_VERSION, EvidenceStore
from app.agents.tools import TOOL_CONTRACT_VERSION


class BusinessExtraction(ListExtraction):
    max_board_height: int | None = Field(default=None, ge=0)
    limit_up_count: int | None = Field(default=None, ge=0)
    matched_count: int | None = Field(default=None, ge=0)


class BusinessReport(Contract):
    verifier_version: Literal["business-facts-v2"] = "business-facts-v2"
    scope: Literal["required_business_facts"] = "required_business_facts"
    case: AssetRef
    verdict: Literal["pass", "fail", "needs_review"]
    release_eligible: Literal[False] = False
    findings: list[EvaluationFinding]
    claim_coverage: float | None = None


def identity(symbol, name):
    # Only whitespace is presentation-equivalent. Codes and other characters stay exact.
    return symbol, "".join(name.split()) if name is not None else None


def _source_rows(world):
    rows = {}
    for recording in world.recordings:
        if recording.tool != "limit_up_events":
            continue
        for row in recording.observation.payload.get("events", []):
            key = (row["trade_date"], row["symbol"])
            if key in rows and rows[key] != row:
                raise ValueError("conflicting baseline rows")
            rows[key] = row
    return rows


def _truth(expected, world, source):
    day = expected["trade_date"]
    if "row_selection" in expected:
        from app.agent_eval.selection import complete_day, select_rows
        return {}, select_rows(complete_day(world, day), expected["row_selection"])
    if "selection" in expected:
        rows=[r for (d,_),r in source.items() if d==day and r["closed_limit"]]
        complete=[r.observation.payload for r in world.recordings if r.tool=="limit_up_events"
                  and r.observation.payload.get("trade_date")==day
                  and r.observation.payload.get("event_status")=="all"
                  and not any(r.arguments.get(k) for k in ("query","market","board_height","highest_only"))
                  and r.observation.payload.get("returned_count")==r.observation.payload.get("matched_count")]
        if not complete:
            raise ValueError("selection requires complete unfiltered baseline")
        selection=expected["selection"]
        if selection=={"market":"chinext","board_height":1}:
            rows=[r for r in rows if r["symbol"].startswith(("300","301")) and r["board_height"]==1]
        elif selection=={"broken_only":True}:
            rows=[r for r in rows if r["break_count"]>0]
        else:
            raise ValueError("unsupported selection")
        return {},rows
    # Require at least one demonstrably complete unfiltered closed-day recording.
    pools = [r.observation.payload for r in world.recordings if r.tool == "limit_up_events"
             and r.observation.state == "ok" and r.observation.payload.get("trade_date") == day
             and r.observation.payload.get("event_status") == "closed"
             and not any(r.arguments.get(k) for k in ("query", "market", "board_height", "min_board_height", "highest_only", "group_by"))
             and r.arguments.get("recent_trade_days", 1) == 1
             and r.observation.payload.get("returned_count") == r.observation.payload.get("matched_count")]
    if "max_board_height" in expected or "limit_up_count" in expected:
        if not pools:
            raise ValueError("no complete closed-day baseline; cannot establish maximum or count")
        rows = pools[0]["events"]
        if "limit_up_count" in expected:
            return {"limit_up_count": len(rows)}, []
        height = max(r["board_height"] for r in rows)
        return {"max_board_height": height}, [r for r in rows if r["board_height"] == height]
    if "matched_count" in expected:
        pools = [r.observation.payload for r in world.recordings if r.tool == "limit_up_events"
                 and r.arguments.get("query") == "评测不存在主题"
                 and r.observation.payload.get("trade_date") == day
                 and r.observation.state == "empty"]
        if not pools or any(p.get("matched_count") != 0 or p.get("events") for p in pools):
            raise ValueError("no genuine empty observation for specified query")
        return {"matched_count":0}, []
    if "members" in expected and all("closed_limit" in r for r in expected["members"]):
        pools = [r.observation.payload for r in world.recordings if r.tool == "limit_up_events"
                 and r.observation.payload.get("trade_date") == day
                 and r.observation.payload.get("market") == "star_market"
                 and r.observation.payload.get("event_status") == "broken_intraday"
                 and r.observation.payload.get("matched_count") == r.observation.payload.get("returned_count")]
        if not pools:
            raise ValueError("no complete intraday-break baseline")
        return {}, pools[0]["events"]
    raise ValueError("unsupported business assertion")


def verify_business_facts(case, world, response, extraction, *, diagnostic_unreviewed=False):
    findings, coverage = [], None
    try:
        if case.profile != world.profile or response.generated_by != VERSION:
            raise ValueError("profile/runtime mismatch")
        if world.tool_contract_version != TOOL_CONTRACT_VERSION or world.evidence_version != EVIDENCE_VERSION:
            raise ValueError("baseline contract mismatch")
        if case.mode == "offline" and (case.world is None or (case.world.id,case.world.version) != (world.world_id,world.world_version)):
            raise ValueError("offline world mismatch")
        if case.mode not in {"offline","live_historical"}:
            raise ValueError("business adapter is not a current-live evaluator")
        _attempts(response)
        if extraction is None:
            raise ValueError("missing business extraction")
        extraction = BusinessExtraction.model_validate_json(extraction.model_dump_json())
        if extraction.answer_digest != digest(response.answer) or response.answer[extraction.start:extraction.start+len(extraction.quote)] != extraction.quote:
            raise ValueError("extraction belongs to another answer/span")
        needed = 2 if case.severity == "P0" else 1
        if diagnostic_unreviewed:
            findings.append(finding("$calibration","needs_review","automatic extraction is provisional"))
        elif (not extraction.inventory_complete or len(set(extraction.inventory_reviewers)) < needed
              or len(set(extraction.extraction_reviewers)) < needed):
            raise ValueError("independent review incomplete")
        if extraction.ambiguous:
            raise ValueError("ambiguous extraction")
        if extraction.additional_claims:
            findings.append(finding("$additional_claims","needs_review","additional statements require separate review"))
        source = _source_rows(world)
        execution = next(t.output for t in response.tool_results if t.name == "react_execution")
        views = [v for t in response.tool_results if t.name == "react_observe" for v in t.output["results"]]
        from app.agent_eval.process_checks import evaluate_process
        process = evaluate_process(case, response)
        checks = {f.assertion_id: f.verdict for f in process.findings}
        verified_compute = {v.get("evidence_id") for v in views
            if checks.get("compute:" + v.get("call_id", "")) == "pass"
            and checks.get("dependency:" + v.get("call_id", "")) == "pass"
            and "$process_trace" not in checks}
        visible, usable = {}, []
        for key, record in execution["evidence"].items():
            tool = record.get("tool")
            if tool not in {"limit_up_events", "market_event_pool", "compute_result"} or record.get("evidence_scope") != CURRENT_SCOPE or record.get("historical_reference"):
                continue
            if tool == "compute_result" and key not in verified_compute:
                continue
            if record.get("schema_version") != EVIDENCE_VERSION or record.get("evidence_id") != key:
                raise ValueError("invalid current evidence")
            payload = record["payload"]
            rows = payload.get("events", payload.get("items", []))
            if tool != "market_event_pool" and any(source.get((r["trade_date"],r["symbol"])) != r for r in rows):
                raise ValueError("current rows drifted from baseline")
            if tool == "market_event_pool":
                for row in rows:
                    raw = source.get((payload.get("trade_date"), row.get("symbol")))
                    if raw is None or any(raw.get(k) != row.get(k) for k in ("symbol", "name", "board_height", "closed_limit", "break_count")):
                        raise ValueError("event-pool identity/values drifted from baseline")
            matching = [v for v in views if v.get("evidence_id") == key]
            for view in matching:
                store = EvidenceStore()
                eid = store.add(tool=tool, payload=payload, state=record["result_state"],
                                arguments=record["arguments"],sources=record.get("sources"))
                replay = store.view(eid, offset=view["offset"], limit=max(1,len(view["rows"])))
                if any(replay[f] != view.get(f) for f in replay.keys()-{"evidence_id","retrieved_at"}):
                    raise ValueError("visible/full evidence mismatch")
                if record["result_state"] in {"ok","empty"}:
                    usable.append((record,view))
                    for row in view["rows"]:
                        visible[(row.get("trade_date", payload.get("trade_date")),row["symbol"])] = row
        for assertion in case.assertions:
            if assertion.evaluator != "fact":
                continue
            if assertion.target != "answer.business_contract" or assertion.kind != "fact_supported":
                findings.append(finding(assertion.id,"needs_review","unsupported assertion"))
                continue
            expected = assertion.expected
            scalars, members = _truth(expected, world, source)
            if any(expected[k] != v for k,v in scalars.items()):
                raise ValueError("expected scalar contradicts baseline")
            expected_members = expected.get("members", [])
            identities = {(r["symbol"],r["name"]) for r in members}
            if identities != {(r["symbol"],r["name"]) for r in expected_members}:
                raise ValueError("expected membership contradicts baseline")
            if any("closed_limit" in r and next(m for m in members if m["symbol"]==r["symbol"])["closed_limit"] != r["closed_limit"] for r in expected_members):
                raise ValueError("expected reclosure flag contradicts baseline")
            actual = [identity(m.symbol, m.name) for m in extraction.members]
            if any(not symbol or not name for symbol,name in actual):
                raise ValueError("business membership requires reviewed code/name identities")
            correct = extraction.trade_date is not None and extraction.trade_date.isoformat() == expected["trade_date"]
            correct = correct and all(getattr(extraction,k) == v for k,v in scalars.items())
            if "members" in expected:
                canonical = {identity(symbol, name) for symbol, name in identities}
                correct = correct and set(actual)==canonical and len(actual)==len(canonical)
                if expected.get("ordered"):
                    correct = correct and actual == [identity(m["symbol"], m["name"]) for m in members]
            grounded = all((expected["trade_date"],r["symbol"]) in visible for r in members)
            if "limit_up_count" in scalars:
                grounded = any(v["metadata"].get("trade_date")==expected["trade_date"]
                    and (v["metadata"].get("event_status")=="closed" or
                         (r["tool"] == "market_event_pool" and v["metadata"].get("event_type") == "limit_up"))
                    and not any(r["arguments"].get(k) for k in ("query", "market", "board_height", "min_board_height", "highest_only"))
                    and v["metadata"].get("matched_count")==scalars["limit_up_count"] for r,v in usable)
            if "matched_count" in scalars:
                grounded = any(r["result_state"]=="empty" and r["arguments"].get("query")=="评测不存在主题"
                    and v["metadata"].get("trade_date")==expected["trade_date"] for r,v in usable)
            verdict = "fail" if not correct else "pass" if grounded else "needs_review"
            findings.append(finding(assertion.id,verdict,"required business values match baseline and evidence" if verdict=="pass"
                else "required business values differ or are missing" if verdict=="fail" else "current evidence support not established"))
    except (ValueError,TypeError,KeyError,AttributeError,StopIteration) as error:
        findings.append(finding("$business_context","needs_review",str(error)))
    if not findings:
        findings.append(finding("$business_context","needs_review","no checks"))
    verdict = "needs_review" if diagnostic_unreviewed or any(f.verdict=="needs_review" for f in findings) else (
        "fail" if any(f.verdict=="fail" for f in findings) else "pass")
    return BusinessReport(case=AssetRef(id=case.case_id,version=case.case_version),verdict=verdict,findings=findings,claim_coverage=coverage)
