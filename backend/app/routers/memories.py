"""Owner-authenticated CRUD for explicit, revocable long-term memories."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.memory_models import MemoryContent, MemoryMutationResult, MemoryRecord, MemoryType, MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository, MemoryConflict, MemoryNotFound
from app.security import current_owner_id

router = APIRouter(prefix="/chat/memories")
Owner = Annotated[str, Depends(current_owner_id)]
OperationId = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]


def _raise_http(error):
    if isinstance(error, MemoryNotFound):
        raise HTTPException(404, "Memory not found") from error
    if isinstance(error, MemoryConflict):
        raise HTTPException(409, str(error)) from error
    raise HTTPException(422, str(error)) from error


@router.get("")
def list_memories(
    response: Response, owner_id: Owner, memory_type: MemoryType | None = None,
    key: str | None = Query(default=None, min_length=1, max_length=120),
    limit: int = Query(default=100, ge=1, le=200),
):
    response.headers["Cache-Control"] = "private, no-store"
    return LongTermMemoryRepository().read_context(
        owner_id, memory_types=[memory_type] if memory_type else None,
        exact_keys=[key] if key else None, limit=limit,
    )


@router.get("/candidates")
def list_memory_candidates(
    owner_id: Owner, response: Response,
    limit: int = Query(default=100, ge=1, le=200),
):
    """List background candidates awaiting explicit confirmation."""

    response.headers["Cache-Control"] = "private, no-store"
    return {"items": LongTermMemoryRepository().list_candidates(owner_id, limit=limit)}


@router.get("/candidates/workbench")
def list_memory_candidate_workbench(
    owner_id: Owner, response: Response,
    limit: int = Query(default=100, ge=1, le=200),
):
    """Group candidate conflicts with active families and exact targets."""

    response.headers["Cache-Control"] = "private, no-store"
    return {
        "groups": LongTermMemoryRepository().list_candidate_workbench(
            owner_id, limit=limit,
        ),
    }


@router.post("/candidates/{memory_id}/confirm", response_model=MemoryMutationResult)
def confirm_memory_candidate(
    memory_id: str, owner_id: Owner, operation_id: OperationId,
):
    try:
        return LongTermMemoryRepository().confirm_candidate(
            owner_id, memory_id, operation_id,
        )
    except (MemoryConflict, MemoryNotFound, ValueError) as error:
        _raise_http(error)


@router.post("/candidates/{memory_id}/reject", response_model=MemoryMutationResult)
def reject_memory_candidate(
    memory_id: str, owner_id: Owner, operation_id: OperationId,
    expected_revision: int = Query(ge=1),
):
    try:
        return LongTermMemoryRepository().reject_candidate(
            owner_id, memory_id, expected_revision, operation_id,
        )
    except (MemoryConflict, MemoryNotFound, ValueError) as error:
        _raise_http(error)


@router.get("/by-key/{memory_type}/{key}", response_model=MemoryRecord)
def get_memory_by_key(memory_type: MemoryType, key: str, owner_id: Owner, response: Response,
                      include_inactive: bool = False):
    response.headers["Cache-Control"] = "private, no-store"
    memory = LongTermMemoryRepository().get_by_key(owner_id, memory_type, key, include_inactive=include_inactive)
    if memory is None:
        raise HTTPException(404, "Memory not found")
    return memory


@router.get("/{memory_id}", response_model=MemoryRecord)
def get_memory(memory_id: str, owner_id: Owner, response: Response, include_inactive: bool = False):
    response.headers["Cache-Control"] = "private, no-store"
    memory = LongTermMemoryRepository().get(owner_id, memory_id, include_inactive=include_inactive)
    if memory is None:
        raise HTTPException(404, "Memory not found")
    return memory


@router.put("/{memory_type}/{key}", response_model=MemoryMutationResult)
def put_memory(
    memory_type: MemoryType, key: str, body: MemoryContent, owner_id: Owner, operation_id: OperationId,
    expected_owner_revision: int | None = Query(default=None, ge=0),
):
    try:
        write = MemoryWrite(memory_type=memory_type, key=key, **body.model_dump())
        return LongTermMemoryRepository().put(owner_id, write, operation_id,
                                              expected_owner_revision=expected_owner_revision)
    except (MemoryConflict, MemoryNotFound, ValueError) as error:
        _raise_http(error)


@router.delete("/{memory_id}", response_model=MemoryMutationResult)
def delete_memory(
    memory_id: str, owner_id: Owner, operation_id: OperationId,
    expected_revision: int = Query(ge=1), expected_owner_revision: int | None = Query(default=None, ge=0),
):
    try:
        return LongTermMemoryRepository().delete(owner_id, memory_id, expected_revision, operation_id,
                                                 expected_owner_revision=expected_owner_revision)
    except (MemoryConflict, MemoryNotFound, ValueError) as error:
        _raise_http(error)
