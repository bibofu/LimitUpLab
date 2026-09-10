"""Versioned schema and strict loader for the Agent golden dataset."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


GOLDEN_DATASET_VERSION = "agent-golden-v1"
GOLDEN_DATASET_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent_golden_dataset.json"
REQUIRED_CASE_FIELDS = frozenset(
    {
        "question",
        "expected_capabilities",
        "required_tools",
        "expected_parameters",
        "required_facts",
        "must_include",
        "must_not_include",
        "allow_refusal",
    }
)


@dataclass(frozen=True)
class GoldenEvalCase:
    """One auditable user question and its four-layer behavioral contract."""

    case_id: str
    category: str
    question: str
    expected_capabilities: list[str]
    required_tools: list[str]
    expected_parameters: dict[str, dict[str, object]]
    required_facts: list[str]
    must_include: list[str]
    must_not_include: list[str]
    allow_refusal: bool
    conversation_id: str | None = None
    intent_hint: str | None = None
    trade_date: str | None = None
    symbol: str | None = None
    simulate_tool_failure: list[str] = field(default_factory=list)


def load_golden_cases(path: Path) -> tuple[str, list[GoldenEvalCase]]:
    """Load and strictly validate a 50-80 question golden dataset."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    version = str(payload.get("version") or "")
    if not version:
        raise ValueError("golden dataset version is required")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not 50 <= len(raw_cases) <= 80:
        raise ValueError("golden dataset must contain 50 to 80 cases")
    cases: list[GoldenEvalCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"golden case {index} must be an object")
        missing = REQUIRED_CASE_FIELDS - item.keys()
        if missing:
            raise ValueError(
                f"golden case {index} is missing fields: {', '.join(sorted(missing))}"
            )
        case = GoldenEvalCase(**item)
        if not case.case_id or case.case_id in seen_ids:
            raise ValueError(f"golden case id must be unique: {case.case_id!r}")
        if not case.category or not case.question.strip():
            raise ValueError(f"golden case {case.case_id} needs category and question")
        seen_ids.add(case.case_id)
        cases.append(case)
    return version, cases
