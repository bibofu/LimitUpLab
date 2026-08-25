"""Deterministic regression runner for Agent Query Contract v2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.query_contract import build_limit_up_query_contract


@dataclass(frozen=True)
class QueryContractEvalCase:
    """One natural-language contract interpretation case."""

    case_id: str
    message: str
    expected: dict[str, Any]
    request_trade_date: str | None = None
    planner_arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryContractEvalResult:
    """Contract output and assertion failures for one case."""

    case_id: str
    passed: bool
    failures: list[str]
    actual: dict[str, Any]


@dataclass(frozen=True)
class QueryContractEvalSuite:
    """Aggregate result for the deterministic contract regression set."""

    total: int
    passed: int
    failed: int
    results: list[QueryContractEvalResult]

    @property
    def ok(self) -> bool:
        return self.failed == 0


def load_query_contract_eval_cases(path: Path) -> list[QueryContractEvalCase]:
    """Load query-contract cases from a UTF-8 JSON fixture."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [QueryContractEvalCase(**item) for item in payload["cases"]]


def run_query_contract_eval_suite(
    cases: list[QueryContractEvalCase],
) -> QueryContractEvalSuite:
    """Run all contract cases without an LLM or network dependency."""

    results: list[QueryContractEvalResult] = []
    for case in cases:
        contract = build_limit_up_query_contract(
            case.message,
            request_trade_date=(
                date.fromisoformat(case.request_trade_date)
                if case.request_trade_date
                else None
            ),
            planner_arguments=case.planner_arguments,
        )
        actual = contract.to_dict()
        failures = [
            f"{key} expected {expected!r}, got {actual.get(key)!r}"
            for key, expected in case.expected.items()
            if actual.get(key) != expected
        ]
        results.append(
            QueryContractEvalResult(
                case_id=case.case_id,
                passed=not failures,
                failures=failures,
                actual=actual,
            )
        )
    passed = sum(1 for result in results if result.passed)
    return QueryContractEvalSuite(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        results=results,
    )


def query_contract_eval_report(suite: QueryContractEvalSuite) -> dict[str, Any]:
    """Serialize a contract suite for CLI and CI artifacts."""

    return {
        "version": "limit-up-query-v2",
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "results": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "failures": result.failures,
                "actual": result.actual,
            }
            for result in suite.results
        ],
    }
