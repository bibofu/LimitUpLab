"""Administrator observability summary shapes."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class ObservabilityBucket(BaseModel):
    """One Shanghai-day slice of traffic, feedback and review activity."""

    date: date
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    rejected_count: int = 0
    distinct_owners: int = 0
    answered_count: int = 0
    partial_count: int = 0
    answer_error_count: int = 0
    clarify_refuse_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    measured_request_count: int = 0
    incomplete_token_count: int = 0
    estimated_cost_usd: float = 0.0
    up_count: int = 0
    down_count: int = 0
    case_count: int = 0


class ObservabilityTotals(BaseModel):
    """Period totals with explicit denominators and data-quality counters."""

    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    rejected_count: int = 0
    running_count: int = 0
    distinct_owners: int = 0
    distinct_sessions: int = 0
    answered_count: int = 0
    partial_count: int = 0
    answer_error_count: int = 0
    clarify_refuse_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    measured_request_count: int = 0
    incomplete_token_count: int = 0
    estimated_cost_usd: float = 0.0
    up_count: int = 0
    down_count: int = 0
    vote_count: int = 0
    like_ratio: float | None = None
    feedback_coverage: float | None = None
    down_per_100_answers: float | None = None
    case_count: int = 0
    open_case_count: int = 0
    confirmed_case_count: int = 0
    closed_case_count: int = 0
    sample_job_count: int = 0
    sample_coverage: float | None = None
    mtta_seconds: float | None = None
    mttr_seconds: float | None = None
    p50_duration_ms: float | None = None
    p95_duration_ms: float | None = None
    judge_call_count: int = 0
    judge_succeeded_count: int = 0
    judge_error_count: int = 0
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0
    judge_total_tokens: int = 0
    judge_estimated_cost_usd: float = 0.0
    judge_priced_call_count: int = 0
    judge_incomplete_token_count: int = 0


class ObservabilityReasonCount(BaseModel):
    reason: str
    count: int


class ObservabilitySummary(BaseModel):
    """Protected operational summary for the hidden administrator dashboard."""

    days: int
    period_started_at: datetime
    generated_at: datetime
    timezone: str = "Asia/Shanghai"
    totals: ObservabilityTotals
    buckets: list[ObservabilityBucket] = Field(default_factory=list)
    feedback_reasons: list[ObservabilityReasonCount] = Field(default_factory=list)
    case_status_counts: dict[str, int] = Field(default_factory=dict)
    case_source_counts: dict[str, int] = Field(default_factory=dict)
    job_status_counts: dict[str, int] = Field(default_factory=dict)
    job_kind_counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    generated_by: str
