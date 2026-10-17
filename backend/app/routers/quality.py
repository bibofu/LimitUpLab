"""Owner-scoped feedback and administrator-only human review endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.quality_models import CaseStatus, DeliveryInput, FeedbackInput, QualityConflict, ReviewInput, Severity
from app.repositories.quality_repository import QualityRepository
from app.security import current_owner_id, require_admin_access
from app.services.quality_detection import process_pending


router = APIRouter()
admin = APIRouter(prefix="/api/admin/quality", dependencies=[Depends(require_admin_access)])
Owner = Annotated[str, Depends(current_owner_id)]


def repository():
    return QualityRepository()


Repository = Annotated[QualityRepository, Depends(repository)]


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except KeyError as error:
        raise HTTPException(404, "Quality record not found") from error
    except QualityConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.put("/api/agents/chat/runs/{run_id}/feedback")
def feedback(run_id: str, body: FeedbackInput, owner_id: Owner, repo: Repository):
    return {"feedback": _call(repo.save_feedback, run_id, owner_id, body.model_dump())}


@router.get("/api/agents/chat/sessions/{session_id}/quality")
def session_quality(session_id: str, owner_id: Owner, repo: Repository):
    return _call(repo.session_state, session_id, owner_id)


@router.post("/api/agents/chat/runs/{run_id}/delivery")
def delivery(run_id: str, body: DeliveryInput, owner_id: Owner, repo: Repository):
    return _call(repo.record_delivery, run_id, owner_id, body.event, body.answer_hash)


@admin.get("/cases")
def cases(repo: Repository, status: CaseStatus | None = None, source: str | None = Query(None, max_length=80),
          severity: Severity | None = None, symptom: str | None = Query(None, max_length=200),
          version: str | None = Query(None, max_length=200),
          limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return repo.list_cases(status=status, source=source, severity=severity, symptom=symptom,
                           version=version, limit=limit, offset=offset)


@admin.get("/cases/{case_id}")
def case_detail(case_id: str, repo: Repository):
    return _call(repo.case_detail, case_id)


@admin.patch("/cases/{case_id}")
def review(case_id: str, body: ReviewInput, repo: Repository):
    # Existing authentication uses one administrator key, not named accounts.
    # Never accept a spoofed reviewer id or save the key in an audit record.
    return _call(repo.review_case, case_id, {**body.model_dump(exclude_unset=True), "actor_id": "admin"})


@admin.post("/process")
def process(repo: Repository, limit: int = Query(20, ge=1, le=50)):
    return process_pending(repo, limit=limit)


@admin.get("/jobs")
def jobs(repo: Repository):
    return repo.job_health()


router.include_router(admin)
