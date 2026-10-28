"""API shapes for the administrator review queue and feedback workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ReviewSignalKind = Literal[
    "downvote",
    "deterministic",
    "judge",
    "sample",
    "run_error",
    "grounding",
    "manual",
]
ReviewSeverity = Literal["info", "low", "medium", "high"]
ReviewStatus = Literal[
    "new",
    "confirmed",
    "fixing",
    "verifying",
    "closed",
    "dismissed",
    "insufficient",
]
ReviewSource = Literal[
    "user_feedback",
    "deterministic",
    "judge",
    "sample",
    "manual",
]


class ReviewSignal(BaseModel):
    """One append-only piece of evidence attached to a run or case."""

    signal_id: str
    run_id: str
    case_id: str | None = None
    kind: ReviewSignalKind
    severity: ReviewSeverity
    detector: str
    detector_version: str
    source_ref: str = ""
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    processed_at: datetime | None = None


class ReviewCaseEvent(BaseModel):
    """One human action recorded on a review case."""

    event_id: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    changes: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    actor: str = "admin"
    created_at: datetime


class ReviewCaseSummary(BaseModel):
    """List-friendly case metadata for the review queue."""

    case_id: str
    run_id: str | None = None
    session_id: str
    status: ReviewStatus
    source: ReviewSource
    severity: ReviewSeverity
    intent: str | None = None
    task_status: str | None = None
    question_preview: str
    signal_count: int = 0
    has_new_signal: bool = False
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
    closed_at: datetime | None = None


class ReviewDetectorReport(BaseModel):
    """A completed deterministic or judge report attached to a run."""

    job_id: str
    kind: str
    detector_version: str
    created_at: datetime
    report: dict[str, Any] = Field(default_factory=dict)


class ReviewCaseDetail(ReviewCaseSummary):
    """One case with its frozen answer snapshot, signals and review history."""

    question: str
    answer: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_url: str | None = None
    symptom: str = ""
    root_cause: str = ""
    fix_commit: str = ""
    fix_version: str = ""
    verification: str = ""
    revision: int = 1
    signals: list[ReviewSignal] = Field(default_factory=list)
    events: list[ReviewCaseEvent] = Field(default_factory=list)
    reports: list[ReviewDetectorReport] = Field(default_factory=list)


class ReviewCasesResponse(BaseModel):
    """Filtered review queue for the administrator workspace."""

    cases: list[ReviewCaseSummary] = Field(default_factory=list)
    generated_by: str


class ReviewCaseUpdate(BaseModel):
    """One optimistic, audited review update."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus | None = None
    severity: ReviewSeverity | None = None
    symptom: str | None = Field(default=None, max_length=2000)
    root_cause: str | None = Field(default=None, max_length=4000)
    fix_commit: str | None = Field(default=None, max_length=100)
    fix_version: str | None = Field(default=None, max_length=100)
    verification: str | None = Field(default=None, max_length=4000)
    note: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=1)


class ReviewBulkDismissRequest(BaseModel):
    """Dismiss several new cases with one shared audit note."""

    model_config = ConfigDict(extra="forbid")

    case_ids: list[str] = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)


class ReviewBulkDismissResponse(BaseModel):
    """Counts from one bulk dismissal request."""

    dismissed: int
    skipped: int


class ReviewFeedbackRow(BaseModel):
    """One owner vote shown in the administrator feedback list."""

    run_id: str
    session_id: str
    vote: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list)
    comment: str = ""
    question_preview: str
    task_status: str | None = None
    updated_at: datetime


class ReviewFeedbackResponse(BaseModel):
    """Recent feedback rows for the administrator workspace."""

    feedback: list[ReviewFeedbackRow] = Field(default_factory=list)
    generated_by: str


class ReviewJob(BaseModel):
    """One sampling or detector job in the review outbox."""

    job_id: str
    run_id: str
    kind: Literal["deterministic", "sample", "judge"]
    priority: int
    status: Literal["pending", "processing", "succeeded", "skipped", "failed", "deferred"]
    attempts: int
    available_at: datetime
    detector_version: str
    config_fingerprint: str
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ReviewJobsResponse(BaseModel):
    """Recent review jobs for queue health checks."""

    jobs: list[ReviewJob] = Field(default_factory=list)
    generated_by: str


class ReviewDrainResponse(BaseModel):
    """Counts from one bounded worker pass."""

    sampled_runs: int
    processed_jobs: int
    aggregated_signals: int


class ReviewSampleResponse(BaseModel):
    """Counts from a manual sampling request."""

    enqueued: int


class ReviewRecheckResponse(BaseModel):
    """Jobs queued for one explicit recheck."""

    deterministic_jobs: int
    judge_jobs: int
