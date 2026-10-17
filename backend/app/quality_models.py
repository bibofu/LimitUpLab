"""Validated contracts for user feedback and human quality review."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


VoteReason = Literal["data_error", "intent_error", "incomplete", "unsupported", "expression", "slow"]
CaseStatus = Literal["pending", "confirmed", "in_progress", "verifying", "closed", "duplicate", "not_a_bug", "insufficient_evidence"]
Severity = Literal["low", "medium", "high", "critical"]


class QualityConflict(ValueError):
    """An otherwise valid operation conflicts with durable state."""


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vote: Literal["up", "down"] | None = None
    reasons: list[VoteReason] = Field(default_factory=list, max_length=6)
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def normalize(self):
        self.reasons = list(dict.fromkeys(self.reasons)) if self.vote == "down" else []
        self.comment = self.comment.strip() if self.vote else ""
        return self


class DeliveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["received", "displayed", "interrupted"]
    answer_hash: str = Field(min_length=64, max_length=64)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CaseStatus
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)
    symptom: str | None = Field(default=None, max_length=200)
    root_cause: str | None = Field(default=None, max_length=2000)
    severity: Severity | None = None
    assignee: str | None = Field(default=None, max_length=100)
    fix_version: str | None = Field(default=None, max_length=200)
    duplicate_of: str | None = Field(default=None, max_length=80)


class DetectionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=100)
    severity: Severity = "medium"
    summary: str = Field(min_length=1, max_length=1000)
    evidence: dict = Field(default_factory=dict)
    detector_version: str = Field(min_length=1, max_length=80)
