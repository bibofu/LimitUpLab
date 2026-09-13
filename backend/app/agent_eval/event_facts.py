"""Ordered event-list diagnostics with explicit extraction and visibility abstention."""

from datetime import date
from typing import Literal

from pydantic import Field

from app.agent_eval.evaluators import _attempts, finding
from app.agent_eval.models import AssetRef, Contract, EvaluationFinding, Identifier
from app.agent_eval.recorder import digest
from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import CURRENT_SCOPE, EVIDENCE_VERSION, EvidenceStore
from app.agents.tools import TOOL_CONTRACT_VERSION


class Member(Contract):
    symbol: str | None = None
    name: str | None = None


class ListExtraction(Contract):
    schema_version: Literal["event-list-extraction-v1"] = "event-list-extraction-v1"
    answer_digest: Identifier
    extractor_version: Identifier
    origin: Literal["manual", "model", "contract_test"]
    quote: Identifier
    start: int = Field(ge=0)
    trade_date: date | None = None
    members: list[Member]
    # Human-reviewed inventory counts ALL business claims, not just successful extraction.
    inventory_complete: bool = False
    inventory_reviewers: list[Identifier] = Field(default_factory=list)
    extraction_reviewers: list[Identifier] = Field(default_factory=list)
    additional_claims: list[str] = Field(default_factory=list)
    ambiguous: bool = True


class ListReport(Contract):
    verifier_version: Literal["event-list-facts-v1"] = "event-list-facts-v1"
    scope: Literal["ordered_event_list_only"] = "ordered_event_list_only"
    case: AssetRef
    verdict: Literal["pass", "fail", "needs_review"]
    release_eligible: Literal[False] = False
    claim_coverage: float | None = None
    findings: list[EvaluationFinding]
    support_paths: list[str] = Field(default_factory=list)


def _context(case, world, response, expected):
    if (case.mode != "offline" or case.world is None
            or (case.world.id, case.world.version) != (world.world_id, world.world_version)
            or case.profile != world.profile or response.generated_by != VERSION
            or world.evidence_version != EVIDENCE_VERSION or world.tool_contract_version != TOOL_CONTRACT_VERSION):
        raise ValueError("incompatible case/world/runtime binding")
    _attempts(response)
    def matches(payload):
        return (isinstance(payload, dict) and payload.get("trade_date") == expected["trade_date"]
                and [{"symbol": r["symbol"], "name": r["name"]}
                     for r in payload.get("events", [])[:len(expected["ordered_members"])]]
                == expected["ordered_members"])
    truth = [r.observation.payload for r in world.recordings if r.tool == "limit_up_events"
             and r.observation.state == "ok" and matches(r.observation.payload)]
    if not truth:
        raise ValueError("expected list disagrees with recorded world")
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    records = execution["evidence"]
    observations = [v for t in response.tool_results if t.name == "react_observe"
                    for v in t.output.get("results", [])]
    visible, paths, grounded = set(), [], False
    for key, record in records.items():
        if record.get("tool") != "limit_up_events":
            continue
        if record.get("schema_version") != EVIDENCE_VERSION or record.get("evidence_id") != key:
            raise ValueError("invalid event evidence identity")
        if record.get("evidence_scope") != CURRENT_SCOPE or record.get("historical_reference"):
            continue
        if record.get("result_state") != "ok":
            continue
        payload = record["payload"]
        if not any(payload == r.observation.payload for r in world.recordings if r.tool == "limit_up_events"):
            raise ValueError("current payload differs from frozen recording")
        if not matches(payload):
            continue
        grounded = True
        views = [v for v in observations if v.get("evidence_id") == key]
        if not views:
            raise ValueError("full evidence has no observed view")
        for view in views:
            store = EvidenceStore()
            eid = store.add(tool="limit_up_events", payload=payload, state=record["result_state"],
                            arguments=record["arguments"], sources=record.get("sources"))
            replay = store.view(eid, offset=view["offset"], limit=max(1, len(view["rows"])))
            if any(replay[f] != view.get(f) for f in replay.keys() - {"evidence_id", "retrieved_at"}):
                raise ValueError("visible event view differs from full evidence")
            for i, row in enumerate(view["rows"]):
                visible.add(row["symbol"])
                paths.append(f"{key}/rows/{view['offset'] + i}")
    return grounded, visible, paths


def verify_event_facts(case, world, response, extraction: ListExtraction | None, *, diagnostic_unreviewed=False):
    findings, paths, coverage = [], [], None
    try:
        if extraction is None:
            raise ValueError("no ordered-list extraction")
        extraction = ListExtraction.model_validate_json(extraction.model_dump_json())
        if (extraction.answer_digest != digest(response.answer)
                or response.answer[extraction.start:extraction.start + len(extraction.quote)] != extraction.quote):
            raise ValueError("extraction hash/span does not match answer")
        reviews = 2 if case.severity == "P0" else 1
        if diagnostic_unreviewed:
            findings.append(finding("$calibration", "needs_review", "model extraction is not independently calibrated"))
        elif (not extraction.inventory_complete or len(set(extraction.inventory_reviewers)) < reviews
              or len(set(extraction.extraction_reviewers)) < reviews):
            raise ValueError("independent inventory/extraction review is incomplete")
        if extraction.ambiguous:
            raise ValueError("ambiguous list extraction requires review")
        if extraction.additional_claims:
            findings.append(finding("$additional_claims", "needs_review", "additional claims are outside ordered-list scope"))
        if not diagnostic_unreviewed:
            coverage = 1 / (1 + len(extraction.additional_claims))
        for assertion in case.assertions:
            if assertion.evaluator != "fact":
                continue
            if (assertion.kind != "answer_matches_observation" or assertion.target != "answer.ordered_events"
                    or not isinstance(assertion.expected, dict)
                    or set(assertion.expected) != {"trade_date", "ordered_members"}):
                findings.append(finding(assertion.id, "needs_review", "unsupported list assertion"))
                continue
            expected = assertion.expected
            members = [Member.model_validate(m) for m in expected["ordered_members"]]
            symbols = [m.symbol for m in members]
            if not symbols or any(not s for s in symbols) or len(set(symbols)) != len(symbols):
                raise ValueError("invalid expected member identities")
            grounded, visible, paths = _context(case, world, response, expected)
            actual = []
            for member in extraction.members:
                if member.symbol:
                    actual.append(member.symbol)
                    found = next((m for m in members if m.symbol == member.symbol), None)
                    if found and member.name and member.name != found.name:
                        actual[-1] = "name-code-mismatch:" + member.symbol
                elif member.name:
                    matched = [m.symbol for m in members if m.name == member.name]
                    if len(matched) != 1:
                        raise ValueError("name-only member cannot be resolved unambiguously")
                    actual.append(matched[0])
                else:
                    raise ValueError("member has no identity")
            if extraction.trade_date is None:
                verdict, detail = "needs_review", "answer date could not be extracted safely"
            elif extraction.trade_date.isoformat() != expected["trade_date"] or actual != symbols:
                verdict, detail = "fail", "reviewed ordered identities/date differ (missing, extra, duplicate or wrong order)"
            elif not grounded or not set(symbols) <= visible:
                # Compute and other valid derived routes need a dedicated visibility adapter.
                verdict, detail = "needs_review", "full list visibility not established; inspect pagination/compute route"
            else:
                verdict, detail = "pass", "ordered identities/date match world and current visible evidence"
            findings.append(finding(assertion.id, verdict, detail))
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration) as error:
        findings.append(finding("$list_context", "needs_review", str(error)))
    if not findings:
        findings.append(finding("$list_context", "needs_review", "no supported list assertion"))
    verdict = "fail" if any(f.verdict == "fail" for f in findings) else (
        "needs_review" if any(f.verdict == "needs_review" for f in findings) else "pass")
    return ListReport(case=AssetRef(id=case.case_id, version=case.case_version), verdict=verdict,
                      findings=findings, claim_coverage=coverage, support_paths=paths)
