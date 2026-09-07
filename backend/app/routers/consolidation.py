"""Read-only endpoint for the third premarket research strategy."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from app.consolidation_models import ConsolidationPool, ObservationStrategy
from app.repositories.consolidation_repository import load_consolidation_pool

router = APIRouter()


@router.get("/consolidation", response_model=ConsolidationPool)
def get_consolidation_pool(data_as_of: date | None = None,
                           strategy: ObservationStrategy = "consolidation") -> ConsolidationPool:
    try:
        return load_consolidation_pool(data_as_of, datetime.now(timezone.utc), strategy=strategy)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
