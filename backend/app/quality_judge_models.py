"""Versioned, evidence-bound semantic assessments; never human decisions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.quality_evaluation_models import Verdict
from app.quality_models import Severity


JUDGE_VERSION = "llm-judge-v1"
JUDGE_SCHEMA_VERSION = "quality-judge-schema-v1"
JUDGE_INPUT_VERSION = "quality-judge-input-v1"
JUDGE_PROMPT_VERSION = "quality-judge-prompt-v1"
DIMENSIONS = ("intent_delivery", "factual_grounding", "source_attribution", "investment_boundary")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Quotation(StrictModel):
    ref_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1600, description="逐字引用对应服务端 ref_id 的原文，不能改写。")


class Finding(StrictModel):
    failure_kind: Literal["missing_delivery", "requirement_violation", "contradicted", "prohibited_guidance"]
    summary: str = Field(min_length=1, max_length=1200)
    severity: Severity
    answer_quote: Quotation | None
    requirement_quote: Quotation | None
    evidence_refs: list[str] = Field(min_length=1, max_length=6,
        description="只选择 evidence_catalog 已有 ref_id；不要生成路径、数值或新证据。")

    @model_validator(mode="after")
    def require_quotations(self):
        if self.failure_kind == "missing_delivery":
            if self.requirement_quote is None:
                raise ValueError("Missing delivery requires a quotation of the user's request")
        elif self.answer_quote is None:
            raise ValueError("A visible violation requires an exact answer quotation")
        if self.failure_kind == "requirement_violation" and self.requirement_quote is None:
            raise ValueError("A requirement violation requires the user's request")
        return self


class DimensionJudgement(StrictModel):
    verdict: Verdict
    reason: str = Field(min_length=1, max_length=1600,
        description="先说明本维度实际断言及可见证据范围，再给简短判断；不要求推理过程。")
    unknown_reason: str | None = Field(max_length=1600)
    findings: list[Finding] = Field(max_length=8)

    @model_validator(mode="after")
    def consistent_findings(self):
        if (self.verdict == "fail") != bool(self.findings):
            raise ValueError("Only a failed dimension must contain evidence-bound findings")
        if self.verdict == "unknown" and not (self.unknown_reason or "").strip():
            raise ValueError("Unknown requires a coverage limitation")
        if self.verdict != "unknown" and self.unknown_reason is not None:
            raise ValueError("Only unknown accepts unknown_reason")
        return self


class JudgeSubmission(StrictModel):
    intent_delivery: DimensionJudgement
    factual_grounding: DimensionJudgement
    source_attribution: DimensionJudgement
    investment_boundary: DimensionJudgement


class JudgeEvaluationError(RuntimeError):
    """Safe failure category plus a reproducible input; no raw model text/secrets."""

    def __init__(self, code: str, *, metadata: dict | None = None):
        super().__init__(code)
        self.code = code
        self.metadata = metadata or {}
