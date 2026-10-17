"""Owner-scoped feedback and administrator-only human review endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.quality_models import CaseStatus, DeliveryInput, FeedbackInput, QualityConflict, ReviewInput, Severity
from app.quality_evaluation_models import EVALUATOR_VERSION, Verdict
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


class ReevaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle_id: str | None = Field(default=None, min_length=1, max_length=100)
    normal_sample_rate: float | None = Field(default=None, ge=0, le=1)


@admin.get("/evaluations")
def evaluations(repo: Repository, verdict: Verdict | None = None,
                execution_status: Literal["running", "succeeded", "error", "skipped"] | None = None,
                limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return repo.list_evaluations(verdict=verdict, execution_status=execution_status, limit=limit, offset=offset)


@admin.get("/runs/{run_id}/evaluations")
def evaluation_history(run_id: str, repo: Repository, limit: int = Query(20, ge=1, le=100),
                       offset: int = Query(0, ge=0)):
    return _call(repo.evaluation_history, run_id, limit=limit, offset=offset)


@admin.post("/runs/{run_id}/evaluations")
def enqueue_evaluation(run_id: str, body: ReevaluationInput, repo: Repository):
    config = {"normal_sample_rate": body.normal_sample_rate} if body.normal_sample_rate is not None else None
    return _call(repo.enqueue_evaluation, run_id, job_kind="deterministic", evaluator_version=EVALUATOR_VERSION,
                 config=config, bundle_id=body.bundle_id)


@admin.get("/bundles/{bundle_id}")
def evidence_bundle(bundle_id: str, repo: Repository):
    return _call(repo.get_bundle, bundle_id)


router.include_router(admin)
