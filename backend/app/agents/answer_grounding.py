"""Deterministic claim-to-tool-evidence checks for Agent answer evals."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from app.models import AgentToolTrace


_PAIR_RE = re.compile(
    r"(?P<name>[A-Za-z\u4e00-\u9fff*ＳＴST]{2,20})\s*[（(](?P<symbol>\d{6})[)）]"
)
_DATE_RE = re.compile(r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?!\d)")
_CODE_RE = re.compile(r"(?<!\d)[0368]\d{5}(?!\d)")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>[-+]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|亿元|万元|亿|万|元|只|家|次|板|名|天|日|个|分|点)(?![A-Za-z])"
)
_LABELED_NUMBER_RE = re.compile(
    r"(?P<label>评分|得分|收盘价?|开盘价?|最高价?|最低价?)\s*[:：]?\s*"
    r"(?P<number>[-+]?\d+(?:\.\d+)?)"
)
_REFUSAL_TERMS = ("无法回答", "不能回答", "暂不支持", "没有能力回答")
_NON_EVIDENCE_TOOLS = {
    "agent_plan",
    "query_understanding",
    "llm_tool_planner",
    "tool_policy",
}
_CLAUSE_BOUNDARIES = "；;。！？!?\n"


@dataclass(frozen=True)
class GroundingClaim:
    """One factual claim extracted from the visible answer."""

    text: str
    kind: str
    supported: bool
    evidence_paths: tuple[str, ...] = ()

    # Expose the stored assessment fields as a serializable payload.
    def payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerGroundingResult:
    """Structured deterministic grounding result for one answer."""

    applicable: bool
    passed: bool | None
    claim_count: int
    supported_claim_count: int
    unsupported_claim_count: int
    claim_support_rate: float | None
    claims: tuple[GroundingClaim, ...]
    successful_evidence_tools: tuple[str, ...]
    failed_evidence_tools: tuple[str, ...]
    over_refusal: bool
    tool_failure_hallucination: bool

    # Expose the stored assessment fields as a serializable payload.
    def payload(self) -> dict[str, object]:
        return {
            "applicable": self.applicable,
            "passed": self.passed,
            "claim_count": self.claim_count,
            "supported_claim_count": self.supported_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
            "claim_support_rate": self.claim_support_rate,
            "claims": [claim.payload() for claim in self.claims],
            "successful_evidence_tools": list(self.successful_evidence_tools),
            "failed_evidence_tools": list(self.failed_evidence_tools),
            "over_refusal": self.over_refusal,
            "tool_failure_hallucination": self.tool_failure_hallucination,
        }


@dataclass(frozen=True)
class _EvidenceNumber:
    value: float
    path: str
    category: str


@dataclass(frozen=True)
class _RawClaim:
    text: str
    kind: str
    start: int
    end: int
    value: object
    unit: str = ""
    decimals: int = 0
    entity_symbol: str | None = None
    entity_name: str | None = None
    claim_date: str | None = None


def evaluate_answer_grounding(
    answer: str,
    tool_results: Iterable[AgentToolTrace],
    *,
    user_message: str = "",
) -> AnswerGroundingResult:
    """Verify answer claims against successful structured tool outputs.

    Claims repeated verbatim from the user's question are excluded: they are
    conversation context, not new factual assertions made by the Agent.
    """

    traces = [
        trace for trace in tool_results if trace.name not in _NON_EVIDENCE_TOOLS
    ]
    successes = [
        trace
        for trace in traces
        if trace.result is not None
        and trace.result.status in {"ok", "empty", "partial"}
    ]
    failures = [
        trace
        for trace in traces
        if trace.result is not None and trace.result.status == "error"
    ]
    strings: dict[str, set[str]] = {}
    numbers: list[_EvidenceNumber] = []
    pairs: dict[tuple[str, str], set[str]] = {}
    for trace in successes:
        _collect_evidence(
            trace.result.payload if trace.result is not None else trace.output,
            path=trace.name,
            strings=strings,
            numbers=numbers,
            pairs=pairs,
        )

    raw_claims = _extract_claims(answer)
    question_normalized = _compact(user_message)
    raw_claims = [
        claim
        for claim in raw_claims
        if not question_normalized or _compact(claim.text) not in question_normalized
    ]
    claims = tuple(
        _verify_claim(claim, strings=strings, numbers=numbers, pairs=pairs)
        for claim in raw_claims
    )
    supported = sum(claim.supported for claim in claims)
    unsupported = len(claims) - supported
    applicable = bool(successes and claims)
    passed = unsupported == 0 if applicable else None
    over_refusal = bool(successes) and any(term in answer for term in _REFUSAL_TERMS)
    tool_failure_hallucination = bool(failures and unsupported)
    rate = round(supported / len(claims), 4) if claims else None
    return AnswerGroundingResult(
        applicable=applicable,
        passed=passed,
        claim_count=len(claims),
        supported_claim_count=supported,
        unsupported_claim_count=unsupported,
        claim_support_rate=rate,
        claims=claims,
        successful_evidence_tools=tuple(trace.name for trace in successes),
        failed_evidence_tools=tuple(trace.name for trace in failures),
        over_refusal=over_refusal,
        tool_failure_hallucination=tool_failure_hallucination,
    )


# Walk tool payloads and collect the values that answer claims can be checked against.
def _collect_evidence(
    value: Any,
    *,
    path: str,
    strings: dict[str, set[str]],
    numbers: list[_EvidenceNumber],
    pairs: dict[tuple[str, str], set[str]],
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_evidence(
                item,
                path=f"{path}[{index}]",
                strings=strings,
                numbers=numbers,
                pairs=pairs,
            )
        return
    if isinstance(value, dict):
        symbol = str(value.get("symbol") or "").strip()
        name = str(value.get("name") or "").strip()
        if symbol.isdigit() and len(symbol) == 6 and name:
            pairs.setdefault((name, symbol), set()).add(path)
        for key, item in value.items():
            _collect_evidence(
                item,
                path=f"{path}.{key}",
                strings=strings,
                numbers=numbers,
                pairs=pairs,
            )
        return
    if isinstance(value, bool) or value is None:
        return
    category = _path_category(path)
    if isinstance(value, (int, float)):
        numbers.append(_EvidenceNumber(float(value), path, category))
        strings.setdefault(str(value), set()).add(path)
        return
    text = str(value).strip()
    if not text:
        return
    strings.setdefault(_canonical_string(text), set()).add(path)
    parsed = _parse_number_with_unit(text)
    if parsed is not None:
        number, unit, _ = parsed
        numbers.append(_EvidenceNumber(_base_value(number, unit), path, category))


# Identify explicit dates, symbols, counts and numeric claims in the generated answer.
def _extract_claims(answer: str) -> list[_RawClaim]:
    claims: list[_RawClaim] = []
    occupied: list[tuple[int, int]] = []
    for match in _PAIR_RE.finditer(answer):
        claims.append(
            _RawClaim(
                text=match.group(0),
                kind="stock_entity",
                start=match.start(),
                end=match.end(),
                value=(match.group("name"), match.group("symbol")),
            )
        )
        occupied.append(match.span())
    for kind, pattern in (
        ("date", _DATE_RE),
        ("time", _TIME_RE),
        ("stock_code", _CODE_RE),
    ):
        for match in pattern.finditer(answer):
            if _overlaps(match.span(), occupied):
                continue
            claims.append(
                _RawClaim(
                    text=match.group(0),
                    kind=kind,
                    start=match.start(),
                    end=match.end(),
                    value=_canonical_string(match.group(0)),
                )
            )
            occupied.append(match.span())
    for match in _LABELED_NUMBER_RE.finditer(answer):
        if _overlaps(match.span(), occupied):
            continue
        number_text = match.group("number")
        unit = "分" if match.group("label") in {"评分", "得分"} else "点"
        claims.append(
            _RawClaim(
                text=match.group(0),
                kind="number",
                start=match.start(),
                end=match.end(),
                value=float(number_text),
                unit=unit,
                decimals=len(number_text.partition(".")[2]),
            )
        )
        occupied.append(match.span())
    for match in _NUMBER_RE.finditer(answer):
        if _overlaps(match.span(), occupied):
            continue
        number_text = match.group("number")
        unit = match.group("unit")
        claims.append(
            _RawClaim(
                text=match.group(0),
                kind="number",
                start=match.start(),
                end=match.end(),
                value=_base_value(float(number_text), unit),
                unit=unit,
                decimals=len(number_text.partition(".")[2]),
            )
        )
        occupied.append(match.span())
    # Bind metrics and times to an entity/date in the same clause. Evidence
    # matching later requires compatible record paths, so values cannot be
    # borrowed from another stock or date merely because they exist somewhere
    # in the same tool payload.
    ordered = sorted(claims, key=lambda claim: claim.start)
    contextualized: list[_RawClaim] = []
    for claim in ordered:
        if claim.kind not in {"number", "time"}:
            contextualized.append(claim)
            continue
        clause_start, clause_end = _clause_span(answer, claim.start)
        entity_candidates = [
            item
            for item in ordered
            if item.kind in {"stock_entity", "stock_code"}
            and clause_start <= item.start < clause_end
            and item.start <= claim.start
        ]
        date_candidates = [
            item
            for item in ordered
            if item.kind == "date"
            and clause_start <= item.start < clause_end
            and item.start <= claim.start
        ]
        entity_symbol = None
        entity_name = None
        if entity_candidates:
            entity = max(entity_candidates, key=lambda item: item.start)
            if entity.kind == "stock_entity":
                entity_name, entity_symbol = entity.value  # type: ignore[misc]
            else:
                entity_symbol = entity.text
        claim_date = str(max(date_candidates, key=lambda item: item.start).value) if date_candidates else None
        contextualized.append(
            replace(
                claim,
                entity_symbol=entity_symbol,
                entity_name=entity_name,
                claim_date=claim_date,
            )
        )
    return contextualized


# Compare one extracted answer claim with compatible evidence, allowing the claim-specific numeric
# tolerance.
def _verify_claim(
    claim: _RawClaim,
    *,
    strings: dict[str, set[str]],
    numbers: list[_EvidenceNumber],
    pairs: dict[tuple[str, str], set[str]],
) -> GroundingClaim:
    paths: set[str] = set()
    if claim.kind == "stock_entity":
        paths.update(pairs.get(claim.value, set()))  # type: ignore[arg-type]
        if not paths:
            claim_name, claim_symbol = claim.value  # type: ignore[misc]
            for (evidence_name, evidence_symbol), evidence_paths in pairs.items():
                if claim_symbol == evidence_symbol and str(claim_name).endswith(
                    evidence_name
                ):
                    paths.update(evidence_paths)
    elif claim.kind in {"date", "time", "stock_code"}:
        paths.update(strings.get(str(claim.value), set()))
        if claim.kind == "time" and not paths:
            short_time = str(claim.value)[:5]
            for text, evidence_paths in strings.items():
                if text[:5] == short_time:
                    paths.update(evidence_paths)
        paths = _filter_context_paths(
            paths,
            claim=claim,
            strings=strings,
            pairs=pairs,
        )
    else:
        expected_category = _unit_category(claim.unit)
        tolerance = _claim_tolerance(claim)
        for evidence in numbers:
            if not _categories_compatible(expected_category, evidence.category):
                continue
            evidence_value = evidence.value
            if expected_category == "ratio" and abs(evidence_value) <= 1:
                evidence_value *= 100
            if (
                abs(float(claim.value) - evidence_value) <= tolerance
                and _path_matches_claim_context(
                    evidence.path,
                    claim=claim,
                    strings=strings,
                    pairs=pairs,
                )
            ):
                paths.add(evidence.path)
    return GroundingClaim(
        text=claim.text,
        kind=claim.kind,
        supported=bool(paths),
        evidence_paths=tuple(sorted(paths)),
    )


# Normalize equivalent textual representations before comparing evidence with an answer claim.
def _canonical_string(value: str) -> str:
    if value.count(":"):
        return value.strip()
    return value.strip().replace("/", "-").lstrip("0")


# Remove presentation differences so matching focuses on the text content.
def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


# Check whether a candidate text span intersects a span already assigned to another claim.
def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)


def _clause_span(answer: str, position: int) -> tuple[int, int]:
    """Return the nearest punctuation-delimited clause containing a claim."""

    start = max((answer.rfind(char, 0, position) for char in _CLAUSE_BOUNDARIES), default=-1) + 1
    ends = [answer.find(char, position) for char in _CLAUSE_BOUNDARIES]
    usable = [item for item in ends if item >= 0]
    return start, min(usable) if usable else len(answer)


def _filter_context_paths(
    candidate_paths: set[str],
    *,
    claim: _RawClaim,
    strings: dict[str, set[str]],
    pairs: dict[tuple[str, str], set[str]],
) -> set[str]:
    if claim.kind == "stock_code":
        return candidate_paths
    return {
        path
        for path in candidate_paths
        if _path_matches_claim_context(
            path,
            claim=claim,
            strings=strings,
            pairs=pairs,
        )
    }


def _path_matches_claim_context(
    evidence_path: str,
    *,
    claim: _RawClaim,
    strings: dict[str, set[str]],
    pairs: dict[tuple[str, str], set[str]],
) -> bool:
    entity_paths: set[str] = set()
    if claim.entity_symbol:
        canonical_symbol = _canonical_string(claim.entity_symbol)
        entity_paths.update(strings.get(canonical_symbol, set()))
        if claim.entity_name:
            entity_paths.update(
                pairs.get((claim.entity_name, claim.entity_symbol), set())
            )
            if not entity_paths:
                for (name, symbol), paths in pairs.items():
                    if symbol == claim.entity_symbol and claim.entity_name.endswith(name):
                        entity_paths.update(paths)
    if entity_paths and not any(
        _same_record(evidence_path, path) for path in entity_paths
    ):
        return False

    if claim.claim_date:
        date_paths = strings.get(claim.claim_date, set())
        if not date_paths or not any(
            _same_record(evidence_path, path, allow_parent_scope=True)
            for path in date_paths
        ):
            return False
    return True


def _same_record(
    left: str,
    right: str,
    *,
    allow_parent_scope: bool = False,
) -> bool:
    left_record = _record_path(left)
    right_record = _record_path(right)
    if left_record == right_record:
        return True
    if allow_parent_scope:
        return left_record.startswith(right_record + ".") or right_record.startswith(
            left_record + "."
        )
    return False


def _record_path(path: str) -> str:
    """Strip a leaf field while preserving list indices that identify one row."""

    head, separator, _leaf = path.rpartition(".")
    return head if separator else path


# Separate a numeric claim from its unit so values can be compared on a common scale.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _parse_number_with_unit(value: str) -> tuple[float, str, int] | None:
    match = re.fullmatch(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*(%|亿元|万元|亿|万|元|只|家|次|板|名|天|日|个|分|点)?\s*",
        value,
    )
    if not match:
        return None
    number_text = match.group(1)
    return float(number_text), match.group(2) or "", len(number_text.partition(".")[2])


# Convert a number and its display unit into the base value used for evidence comparison.
def _base_value(number: float, unit: str) -> float:
    if unit in {"亿", "亿元"}:
        return number * 100_000_000
    if unit in {"万", "万元"}:
        return number * 10_000
    return number


# Choose the allowed rounding difference for a numeric answer claim.
def _claim_tolerance(claim: _RawClaim) -> float:
    if claim.unit in {"亿", "亿元"}:
        multiplier = 100_000_000
    elif claim.unit in {"万", "万元"}:
        multiplier = 10_000
    else:
        multiplier = 1
    if claim.unit in {"只", "家", "次", "板", "名", "天", "日", "个"}:
        return 0.0
    return max(1e-7, 0.5 * (10 ** (-claim.decimals)) * multiplier)


# Classify a unit so an amount cannot be accepted as evidence for an unrelated price or
# percentage.
def _unit_category(unit: str) -> str:
    if unit == "%":
        return "ratio"
    if unit in {"亿", "亿元", "万", "万元", "元"}:
        return "money"
    if unit in {"只", "家", "个", "名"}:
        return "count"
    if unit == "次":
        return "occurrence"
    if unit == "板":
        return "board"
    if unit == "分":
        return "score"
    if unit == "点":
        return "point"
    if unit in {"天", "日"}:
        return "duration"
    return "generic"


# Infer a metric's meaning from its path in a structured tool result.
def _path_category(path: str) -> str:
    lowered = path.lower()
    if any(
        token in lowered
        for token in ("pct", "rate", "ratio", "return", "change", "confidence")
    ):
        return "ratio"
    if any(
        token in lowered
        for token in ("amount", "turnover", "market_cap", "money", "value")
    ):
        return "money"
    if "board_height" in lowered:
        return "board"
    if any(token in lowered for token in ("break_count", "open_count", "attempt")):
        return "occurrence"
    if any(
        token in lowered
        for token in ("matched_count", "returned_count", "total", "count", "rank")
    ):
        return "count"
    if "score" in lowered:
        return "score"
    if any(token in lowered for token in ("close", "open", "high", "low", "point")):
        return "point"
    if any(token in lowered for token in ("days", "window", "period")):
        return "duration"
    return "generic"


# Decide whether the evidence metric and the answer claim describe compatible quantities.
def _categories_compatible(expected: str, actual: str) -> bool:
    return actual == expected or actual == "generic"
