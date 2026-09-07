"""Read-only endpoint for the third premarket research strategy."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from app.consolidation_models import ConsolidationPool
from app.repositories.consolidation_repository import load_consolidation_pool

router = APIRouter()


@router.get("/consolidation", response_model=ConsolidationPool)
def get_consolidation_pool(data_as_of: date | None = None) -> ConsolidationPool:
    try:
        return load_consolidation_pool(data_as_of, datetime.now(timezone.utc))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
