"""Deterministic claim-to-tool-evidence checks for Agent answer evals."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
_NON_EVIDENCE_TOOLS = {"agent_plan", "llm_tool_planner", "tool_policy"}


@dataclass(frozen=True)
class GroundingClaim:
    """One factual claim extracted from the visible answer."""

    text: str
    kind: str
    supported: bool
    evidence_paths: tuple[str, ...] = ()

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
        trace for trace in traces if trace.status == "success" and trace.output
    ]
    failures = [trace for trace in traces if trace.status == "error"]
    strings: dict[str, set[str]] = {}
    numbers: list[_EvidenceNumber] = []
    pairs: dict[tuple[str, str], set[str]] = {}
    for trace in successes:
        _collect_evidence(
            trace.output,
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
    return sorted(claims, key=lambda claim: claim.start)


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
    else:
        expected_category = _unit_category(claim.unit)
        tolerance = _claim_tolerance(claim)
        for evidence in numbers:
            if not _categories_compatible(expected_category, evidence.category):
                continue
            evidence_value = evidence.value
            if expected_category == "ratio" and abs(evidence_value) <= 1:
                evidence_value *= 100
            if abs(float(claim.value) - evidence_value) <= tolerance:
                paths.add(evidence.path)
    return GroundingClaim(
        text=claim.text,
        kind=claim.kind,
        supported=bool(paths),
        evidence_paths=tuple(sorted(paths)),
    )


def _canonical_string(value: str) -> str:
    if value.count(":"):
        return value.strip()
    return value.strip().replace("/", "-").lstrip("0")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)


def _parse_number_with_unit(value: str) -> tuple[float, str, int] | None:
    match = re.fullmatch(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*(%|亿元|万元|亿|万|元|只|家|次|板|名|天|日|个|分|点)?\s*",
        value,
    )
    if not match:
        return None
    number_text = match.group(1)
    return float(number_text), match.group(2) or "", len(number_text.partition(".")[2])


def _base_value(number: float, unit: str) -> float:
    if unit in {"亿", "亿元"}:
        return number * 100_000_000
    if unit in {"万", "万元"}:
        return number * 10_000
    return number


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


def _categories_compatible(expected: str, actual: str) -> bool:
    return actual == expected or actual == "generic"
