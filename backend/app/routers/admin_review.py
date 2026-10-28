"""Protected administrator endpoints for the answer review queue."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.repositories import AnswerFeedbackRepository
from app.review_models import (
    ReviewBulkDismissRequest,
    ReviewBulkDismissResponse,
    ReviewCaseDetail,
    ReviewCaseUpdate,
    ReviewCasesResponse,
    ReviewDrainResponse,
    ReviewFeedbackResponse,
    ReviewFeedbackRow,
    ReviewJobsResponse,
    ReviewRecheckResponse,
    ReviewSampleResponse,
    ReviewSeverity,
    ReviewSource,
    ReviewStatus,
)
from app.security import require_admin_access
from app.services.review_cases import (
    ReviewCaseConflict,
    ReviewCaseNotFound,
    ReviewCaseNotExportable,
    ReviewCaseService,
    ReviewCaseValidationError,
)
from app.services.review_jobs import ReviewJobService


router = APIRouter()
JobStatus = Literal["pending", "processing", "succeeded", "skipped", "failed", "deferred"]


@router.get("/cases", response_model=ReviewCasesResponse)
def list_review_cases(
    _admin: Annotated[None, Depends(require_admin_access)],
    status: ReviewStatus | None = Query(default=None),
    source: ReviewSource | None = Query(default=None),
    severity: ReviewSeverity | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ReviewCasesResponse:
    """Return the filtered human review queue."""

    return ReviewCasesResponse(
        cases=ReviewCaseService().list_cases(
            status=status,
            source=source,
            severity=severity,
            limit=limit,
        ),
        generated_by="review-cases-v1",
    )


@router.get("/cases/{case_id}", response_model=ReviewCaseDetail)
def get_review_case(
    case_id: str,
    _admin: Annotated[None, Depends(require_admin_access)],
) -> ReviewCaseDetail:
    """Return one case with its answer snapshot, signals and audit history."""

    detail = ReviewCaseService().get_case(case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Review case not found.")
    return detail


@router.patch("/cases/{case_id}", response_model=ReviewCaseDetail)
def update_review_case(
    case_id: str,
    payload: ReviewCaseUpdate,
    _admin: Annotated[None, Depends(require_admin_access)],
) -> ReviewCaseDetail:
    """Apply one audited status or conclusion change."""

    try:
        return ReviewCaseService().update_case(case_id, payload)
    except ReviewCaseNotFound:
        raise HTTPException(status_code=404, detail="Review case not found.") from None
    except ReviewCaseConflict:
        raise HTTPException(
            status_code=409,
            detail="Review case changed; reload before saving.",
        ) from None
    except ReviewCaseValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.post("/cases/bulk-dismiss", response_model=ReviewBulkDismissResponse)
def bulk_dismiss_review_cases(
    payload: ReviewBulkDismissRequest,
    _admin: Annotated[None, Depends(require_admin_access)],
) -> ReviewBulkDismissResponse:
    """Dismiss several new cases with one shared audit note."""

    try:
        result = ReviewCaseService().bulk_dismiss(payload.case_ids, payload.note)
    except ReviewCaseValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return ReviewBulkDismissResponse(**result)


@router.get("/cases/{case_id}/export", response_class=PlainTextResponse)
def export_review_case(
    case_id: str,
    _admin: Annotated[None, Depends(require_admin_access)],
) -> PlainTextResponse:
    """Export one reviewed case in the badCase.md format."""

    try:
        content = ReviewCaseService().export_markdown(case_id)
    except ReviewCaseNotFound:
        raise HTTPException(status_code=404, detail="Review case not found.") from None
    except ReviewCaseNotExportable as error:
        raise HTTPException(
            status_code=409,
            detail=f"Case status '{error.args[0]}' is not exportable.",
        ) from None
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{case_id}.md"'},
    )


@router.get("/feedback", response_model=ReviewFeedbackResponse)
def list_review_feedback(
    _admin: Annotated[None, Depends(require_admin_access)],
    limit: int = Query(default=50, ge=1, le=200),
) -> ReviewFeedbackResponse:
    """Return recent votes with question previews for triage."""

    rows = AnswerFeedbackRepository().list_recent(limit=limit)
    return ReviewFeedbackResponse(
        feedback=[ReviewFeedbackRow.model_validate(item) for item in rows],
        generated_by="review-feedback-v1",
    )


@router.get("/jobs", response_model=ReviewJobsResponse)
def list_review_jobs(
    _admin: Annotated[None, Depends(require_admin_access)],
    status: JobStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ReviewJobsResponse:
    """Return recent sampling and detector jobs."""

    return ReviewJobsResponse(
        jobs=ReviewJobService().list_jobs(status=status, limit=limit),
        generated_by="review-jobs-v1",
    )


@router.post("/jobs/drain", response_model=ReviewDrainResponse)
def drain_review_jobs(
    _admin: Annotated[None, Depends(require_admin_access)],
    limit: int = Query(default=10, ge=1, le=50),
) -> ReviewDrainResponse:
    """Run one bounded worker pass without waiting for the background loop."""

    result = ReviewJobService().run_once(limit=limit)
    return ReviewDrainResponse(**result)


@router.post("/sample", response_model=ReviewSampleResponse)
def sample_review_runs(
    _admin: Annotated[None, Depends(require_admin_access)],
    limit: int = Query(default=5, ge=1, le=50),
) -> ReviewSampleResponse:
    """Add recent unsampled answers to the review queue."""

    return ReviewSampleResponse(
        enqueued=ReviewJobService().enqueue_manual_sample(limit=limit),
    )


@router.post("/runs/{run_id}/recheck", response_model=ReviewRecheckResponse)
def recheck_review_run(
    run_id: str,
    _admin: Annotated[None, Depends(require_admin_access)],
) -> ReviewRecheckResponse:
    """Queue fresh deterministic and enabled judge jobs for one run."""

    try:
        result = ReviewJobService().enqueue_recheck(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.") from None
    return ReviewRecheckResponse(**result)
