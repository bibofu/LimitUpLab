"""Reviewed extraction boundary and narrow summary fact verification; no regex/LLM."""

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

from pydantic import Field, model_validator

from app.agent_eval.evaluators import _attempts, finding
from app.agent_eval.models import AssetRef, CaseSpec, Contract, EvaluationFinding, Identifier, WorldSpec
from app.agent_eval.recorder import digest
from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import CURRENT_SCOPE, EVIDENCE_VERSION, EvidenceStore
from app.agents.tools import TOOL_CONTRACT_VERSION
from app.models import AgentChatResponse


METRICS = {"limit_up_count", "first_board_count", "continued_board_count", "unsealed_count", "limit_down_count"}
UNITS = {"家": ("count", 1), "只": ("count", 1), "个": ("count", 1),
         "万家": ("count", 10000), "万只": ("count", 10000),
         "元": ("money", 1), "万元": ("money", 10000), "亿元": ("money", 100000000),
         "%": ("percent", 1), "百分点": ("percentage_point", 1)}


class ClaimSlot(Contract):
    id: Identifier
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: Identifier
    numeric: bool
    relational: bool


class NumericClaim(Contract):
    slot_id: Identifier
    entity: Identifier
    trade_date: date
    metric: Identifier
    value_text: Identifier
    unit: Identifier
    certainty: Literal["exact", "approximate", "uncertain"] = "uncertain"


class Extraction(Contract):
    schema_version: Literal["fact-extraction-v1"] = "fact-extraction-v1"
    answer_digest: Identifier
    extractor_version: Identifier
    origin: Literal["manual", "model", "contract_test"]
    inventory_complete: bool = False
    inventory_reviewers: list[Identifier] = Field(default_factory=list)
    extraction_reviewers: list[Identifier] = Field(default_factory=list)
    # Inventory is independently annotated, never inferred from the extracted claims.
    inventory: list[ClaimSlot]
    claims: list[NumericClaim]

    @model_validator(mode="after")
    def references(self):
        slots = [slot.id for slot in self.inventory]
        claims = [claim.slot_id for claim in self.claims]
        if len(slots) != len(set(slots)) or len(claims) != len(set(claims)):
            raise ValueError("duplicate inventory/claim slot ID")
        if not set(claims) <= set(slots):
            raise ValueError("claim references unknown inventory slot")
        return self


class FactReport(Contract):
    verifier_version: Literal["summary-facts-v1"] = "summary-facts-v1"
    normalizer_version: Literal["decimal-units-v1"] = "decimal-units-v1"
    scope: Literal["reviewed_summary_numeric_claims"] = "reviewed_summary_numeric_claims"
    case: AssetRef
    verdict: Literal["pass", "fail", "needs_review"]
    release_eligible: Literal[False] = False
    claim_coverage: float | None = None
    numeric_coverage: float | None = None
    relation_coverage: float | None = None
    findings: list[EvaluationFinding] = Field(min_length=1)
    support_paths: dict[str, list[str]] = Field(default_factory=dict)


def normalize_number(text: str, unit: str) -> tuple[str, Decimal]:
    text = text.strip()
    if not text or any(char not in "0123456789+-.eE" for char in text) or unit not in UNITS:
        raise ValueError("unrecognized numeric token or unit")
    try:
        number = Decimal(text)
        if not number.is_finite() or abs(number.adjusted()) > 100:
            raise ValueError("nonfinite or excessive numeric magnitude")
        family, scale = UNITS[unit]
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + 10)
            return family, number * scale
    except InvalidOperation as error:
        raise ValueError("invalid decimal token") from error


def _summary(payload):
    if not isinstance(payload, dict) or "trade_date" not in payload:
        return {}
    day = date.fromisoformat(payload["trade_date"])
    facts = {}
    for metric in METRICS:
        value = payload.get(metric)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ValueError("summary count has incompatible shape")
        facts[("A-share-market", day, metric)] = Decimal(value)
    return facts


def _merge(target, incoming):
    for key, value in incoming.items():
        if key in target and target[key] != value:
            raise ValueError("conflicting facts require fixture/trace review")
        target[key] = value


def _fact_context(case, world, response):
    if (case.mode != "offline" or case.world is None
            or (case.world.id, case.world.version) != (world.world_id, world.world_version)
            or case.profile != world.profile or response.generated_by != VERSION
            or world.evidence_version != EVIDENCE_VERSION or world.tool_contract_version != TOOL_CONTRACT_VERSION):
        raise ValueError("incompatible case/world/runtime/evidence binding")
    calls, _ = _attempts(response)
    truth, current, visible, paths = {}, {}, {}, {}
    for recording in world.recordings:
        if recording.tool == "market_summary" and recording.observation.state in {"ok", "partial", "empty"}:
            _merge(truth, _summary(recording.observation.payload))
    if not truth:
        raise ValueError("world has no supported summary truth")
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    records = execution.get("evidence")
    if not isinstance(records, dict):
        raise ValueError("missing full evidence records")
    observations = [view for t in response.tool_results if t.name == "react_observe"
                    for view in t.output.get("results", [])]
    observed_ids = {view.get("call_id") for view in observations}
    if any(call["id"] not in observed_ids for call in calls):
        raise ValueError("incomplete observation trace")
    for key, record in records.items():
        if record.get("tool") != "market_summary":
            continue
        if record.get("schema_version") != EVIDENCE_VERSION or record.get("evidence_id") != key:
            raise ValueError("incompatible evidence record")
        if record.get("evidence_scope") not in {CURRENT_SCOPE, "conversation_history"}:
            raise ValueError("missing or unknown evidence scope")
        if record.get("historical_reference") or record.get("evidence_scope") != CURRENT_SCOPE:
            continue
        if record.get("result_state") not in {"ok", "partial", "empty"}:
            continue
        facts = _summary(record["payload"])
        if any(identity not in truth or truth[identity] != value for identity, value in facts.items()):
            raise ValueError("current evidence differs from frozen truth")
        matching = [view for view in observations if view.get("evidence_id") == key]
        if not matching:
            raise ValueError("current summary record has no observed view")
        _merge(current, facts)
        for view in matching:
            if (view.get("tool") != "market_summary" or view.get("evidence_scope") != CURRENT_SCOPE
                    or view.get("historical_reference") or view.get("result_state") != record["result_state"]):
                raise ValueError("view scope/state disagrees with current record")
            store = EvidenceStore()
            replay_id = store.add(tool="market_summary", payload=record["payload"],
                state=record["result_state"], arguments=record["arguments"], sources=record.get("sources"))
            replay = store.view(replay_id, offset=view["offset"], limit=max(1, len(view["rows"])))
            for field in replay.keys() - {"evidence_id", "retrieved_at"}:
                if replay[field] != view.get(field):
                    raise ValueError("observed view cannot be reproduced from current evidence")
            containers = [("metadata", view.get("metadata", {}))]
            containers += [(f"rows/{i}", row) for i, row in enumerate(view.get("rows", []))]
            for path, container in containers:
                found = _summary(container)
                if any(identity not in facts or facts[identity] != value for identity, value in found.items()):
                    raise ValueError("visible facts disagree with full current evidence")
                _merge(visible, found)
                for identity in found:
                    paths.setdefault(identity, []).append(f"{key}/{path}/{identity[2]}")
    return truth, current, visible, paths


def verify_summary_facts(case: CaseSpec, world: WorldSpec, response: AgentChatResponse,
                         extraction: Extraction | None) -> FactReport:
    findings, support = [], {}
    coverage = numeric = relation = None
    try:
        if extraction is None:
            raise ValueError("no reviewed extraction; automatic Chinese extraction is not implemented")
        extraction = Extraction.model_validate_json(extraction.model_dump_json())
        if extraction.answer_digest != digest(response.answer):
            raise ValueError("extraction belongs to a different answer")
        required = 2 if case.severity == "P0" else 1
        if (not extraction.inventory_complete or len(set(extraction.inventory_reviewers)) < required
                or len(set(extraction.extraction_reviewers)) < required):
            raise ValueError("independent inventory/extraction review is incomplete")
        for slot in extraction.inventory:
            if slot.end <= slot.start or slot.end > len(response.answer) or response.answer[slot.start:slot.end] != slot.quote:
                raise ValueError("claim inventory span does not match answer")
        resolved = {claim.slot_id for claim in extraction.claims}
        def ratio(slots):
            return sum(slot.id in resolved for slot in slots) / len(slots) if slots else None
        coverage = ratio(extraction.inventory)
        numeric = ratio([s for s in extraction.inventory if s.numeric])
        relation = ratio([s for s in extraction.inventory if s.relational])
        if coverage != 1:
            findings.append(finding("$extraction", "needs_review", "zero or incomplete independently inventoried claim coverage"))
        truth, current, visible, paths = _fact_context(case, world, response)
        passed = set()
        for claim in extraction.claims:
            identity = (claim.entity, claim.trade_date, claim.metric)
            verdict, detail = "needs_review", "metric or certainty is outside current verifier scope"
            if claim.metric in METRICS and claim.certainty == "exact":
                try:
                    family, number = normalize_number(claim.value_text, claim.unit)
                    if family != "count":
                        verdict, detail = "fail", "unit dimension contradicts count metric"
                    elif identity not in truth or truth[identity] != number:
                        verdict, detail = "fail", "entity/date/metric/value contradicts or lacks frozen truth"
                    elif identity not in current or identity not in visible:
                        verdict, detail = "fail", "world-correct claim lacks current visible evidence"
                    else:
                        verdict, detail = "pass", "reviewed claim matches world, current evidence and visible facts"
                        passed.add(identity)
                        support[claim.slot_id] = paths[identity]
                except ValueError:
                    verdict, detail = "needs_review", "numeric token or unit cannot be normalized safely"
            findings.append(finding("claim:" + claim.slot_id, verdict, detail))
        for assertion in case.assertions:
            if assertion.evaluator != "fact":
                continue
            expected = assertion.expected
            if (assertion.kind != "fact_supported" or assertion.target != "answer"
                    or not isinstance(expected, dict) or "trade_date" not in expected
                    or not set(expected) - {"trade_date"} or not set(expected) <= METRICS | {"trade_date"}):
                findings.append(finding(assertion.id, "needs_review", "unsupported expected fact contract"))
                continue
            identities = _summary(expected)
            if not identities or any(truth.get(key) != value for key, value in identities.items()):
                verdict, detail = "needs_review", "expected facts are incomplete or disagree with world"
            elif set(identities) <= passed:
                verdict, detail = "pass", "required facts are present and grounded"
            elif coverage == 1 and all(item.verdict != "needs_review" for item in findings):
                verdict, detail = "fail", "reviewed complete extraction does not deliver required facts"
            else:
                verdict, detail = "needs_review", "required fact delivery cannot yet be determined"
            findings.append(finding(assertion.id, verdict, detail))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        findings.append(finding("$fact_context", "needs_review", str(error)))
    if not findings:
        findings.append(finding("$extraction", "needs_review", "no scorable claims"))
    verdict = "fail" if any(f.verdict == "fail" for f in findings) else (
        "needs_review" if any(f.verdict == "needs_review" for f in findings) else "pass")
    return FactReport(case=AssetRef(id=case.case_id, version=case.case_version), verdict=verdict,
                      claim_coverage=coverage, numeric_coverage=numeric, relation_coverage=relation,
                      findings=findings, support_paths=support)
