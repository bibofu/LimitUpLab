"""Human double-label calibration contract for the Chat Eval V2 LLM Judge."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


CALIBRATION_VERSION = "chat-eval-judge-calibration-v1"
JUDGE_DIMENSIONS = (
    "relevance",
    "completeness",
    "explanation",
    "uncertainty",
    "concision",
)


class CalibrationScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevance: int = Field(ge=0, le=2)
    completeness: int = Field(ge=0, le=2)
    explanation: int = Field(ge=0, le=2)
    uncertainty: int = Field(ge=0, le=2)
    concision: int = Field(ge=0, le=2)


class JudgeCalibrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    human_a: CalibrationScores
    human_b: CalibrationScores
    judge: CalibrationScores


class JudgeCalibrationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    judge_model: str
    prompt_version: str
    items: list[JudgeCalibrationItem]

    @model_validator(mode="after")
    def validate_exact_calibration_set(self) -> "JudgeCalibrationDataset":
        if self.version != CALIBRATION_VERSION:
            raise ValueError(f"version must be {CALIBRATION_VERSION}")
        if len(self.items) != 50:
            raise ValueError("Judge calibration requires exactly 50 items")
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Judge calibration item ids must be unique")
        return self


def evaluate_judge_calibration(path: Path) -> dict[str, Any]:
    """Require human agreement and Judge agreement before enabling a release gate."""

    dataset = JudgeCalibrationDataset.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    dimensions: dict[str, dict[str, float]] = {}
    for dimension in JUDGE_DIMENSIONS:
        human_a = [getattr(item.human_a, dimension) for item in dataset.items]
        human_b = [getattr(item.human_b, dimension) for item in dataset.items]
        judge = [getattr(item.judge, dimension) for item in dataset.items]
        agreed = [left == right for left, right in zip(human_a, human_b)]
        comparable = [index for index, value in enumerate(agreed) if value]
        judge_matches = sum(
            judge[index] == human_a[index] for index in comparable
        )
        dimensions[dimension] = {
            "cohen_kappa": round(_cohen_kappa(human_a, human_b), 4),
            "human_agreement": round(sum(agreed) / len(agreed), 4),
            "judge_consensus_agreement": round(
                judge_matches / len(comparable), 4
            )
            if comparable
            else 0.0,
        }
    passed = all(
        metrics["cohen_kappa"] >= 0.70
        and metrics["human_agreement"] >= 0.80
        and metrics["judge_consensus_agreement"] >= 0.80
        for metrics in dimensions.values()
    )
    return {
        "status": "calibrated" if passed else "failed",
        "passed": passed,
        "version": dataset.version,
        "judge_model": dataset.judge_model,
        "prompt_version": dataset.prompt_version,
        "item_count": len(dataset.items),
        "dimensions": dimensions,
    }


def _cohen_kappa(left: list[int], right: list[int]) -> float:
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in {0, 1, 2}
    )
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)
