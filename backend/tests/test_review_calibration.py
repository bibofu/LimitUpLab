"""Judge calibration math and the starter labeled dataset."""

import json
from pathlib import Path

from app.services.llm_provider import LLMResult
from app.services.review_calibration import (
    calibration_gate,
    cohen_kappa,
    summarize_calibration,
)
from app.services.review_judge import DIMENSIONS
from scripts.evaluate_answer_review import evaluate_cases


DATASET = Path(__file__).resolve().parents[1] / "evals" / "answer_review_cases.json"


class AlwaysPassProvider:
    def generate_function_call(self, *args, **kwargs):
        dimensions = [
            {
                "dimension": dimension,
                "verdict": "pass",
                "reason": "符合要求",
                "evidence_refs": [],
            }
            for dimension in DIMENSIONS
        ]
        return LLMResult(
            content=json.dumps({"dimensions": dimensions, "notes": ""}),
            model="fake-judge",
            provider="fake",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


def test_cohen_kappa_is_chance_corrected():
    assert cohen_kappa(["pass", "fail", "pass"], ["pass", "fail", "pass"]) == 1.0
    assert cohen_kappa(["pass", "pass", "fail", "fail"], ["pass", "pass", "pass", "pass"]) == 0.0
    assert cohen_kappa([], []) is None


def test_calibration_summary_and_gate_require_per_dimension_agreement():
    cases = [
        {
            "case_id": f"cal-{index}",
            "labels": {dimension: "pass" for dimension in DIMENSIONS},
        }
        for index in range(3)
    ]
    predictions = {
        case["case_id"]: {dimension: "pass" for dimension in DIMENSIONS}
        for case in cases
    }
    summary = summarize_calibration(cases, predictions)
    assert summary["overall"]["kappa"] == 1.0
    gate = calibration_gate(summary, minimum_cases=3, minimum_kappa=0.6)
    assert gate["passed"] is True
    assert calibration_gate(summary, minimum_cases=30)["passed"] is False


def test_evaluate_cases_scores_exact_agreement():
    cases = [
        {
            "case_id": f"cal-{index}",
            "question": "2026-10-28 涨停家数是多少？",
            "answer": "涨停 69 家。",
            "context": {
                "task_status": "complete",
                "intent": "market_summary",
                "tool_calls": ["limit_up_events"],
                "warnings": [],
            },
            "evidence": {"tool_result_count": 1, "token_usage_complete": True},
            "labels": {dimension: "pass" for dimension in DIMENSIONS},
        }
        for index in range(3)
    ]
    report = evaluate_cases(
        cases,
        AlwaysPassProvider(),
        minimum_cases=3,
        minimum_kappa=0.6,
    )
    assert report["summary"]["overall"]["kappa"] == 1.0
    assert report["gate"]["passed"] is True
    assert not report["errors"]


def test_starter_dataset_is_complete():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    assert len(cases) >= 30
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        assert set(case["labels"]) == set(DIMENSIONS)
        assert all(value in {"pass", "fail", "unknown"} for value in case["labels"].values())
