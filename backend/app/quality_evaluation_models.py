"""Versioned machine assessments, separate from human case decisions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.quality_models import Severity


EVALUATOR_VERSION = "deterministic-v2"
Verdict = Literal["pass", "fail", "unknown"]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str = Field(min_length=1, max_length=160)
    dimension: str = Field(min_length=1, max_length=80)
    verdict: Verdict
    severity: Severity = "medium"
    summary: str = Field(min_length=1, max_length=2000)
    evidence: dict = Field(default_factory=dict)
    unknown_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_unknown_reason(self):
        if self.verdict == "unknown" and not (self.unknown_reason or "").strip():
            raise ValueError("An unknown check must explain the missing coverage")
        return self


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluator_kind: str = Field(default="deterministic", min_length=1, max_length=80)
    evaluator_version: str = Field(default=EVALUATOR_VERSION, min_length=1, max_length=160)
    execution_status: Literal["succeeded", "error", "skipped"]
    verdict: Verdict
    checks: list[CheckResult] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def separate_execution_and_findings(self):
        if self.execution_status != "succeeded":
            if self.verdict != "unknown":
                raise ValueError("An unsuccessful evaluation cannot pass or fail an answer")
        else:
            expected = aggregate_verdict(self.checks)
            if self.verdict != expected:
                raise ValueError("Report verdict must summarize its recorded checks")
        return self


def aggregate_verdict(checks: list[CheckResult]) -> Verdict:
    """Passing checks describe their scope only, never overall answer correctness."""
    if any(check.verdict == "fail" for check in checks):
        return "fail"
    if not checks or any(check.verdict == "unknown" for check in checks):
        return "unknown"
    return "pass"
