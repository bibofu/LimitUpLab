"""Public endpoints for the post-limit strategy list and evidence."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.repositories import SQLiteStrategyRepository
from app.services.strategy_catalog import STRATEGY_IDS, get_strategy_definition
from app.services.strategy_platform import (
    build_strategy_catalog,
    materialize_strategy_run,
    strategy_statistics,
    strategy_stock_path,
)
from app.strategy_models import (
    StrategyCatalogResponse,
    StrategyHistoryResponse,
    StrategyRunSnapshot,
    StrategyStatisticsResponse,
    StrategyStockResponse,
)

router = APIRouter()


def _definition(strategy_id: str):
    if strategy_id not in STRATEGY_IDS:
        raise HTTPException(status_code=404, detail="Unknown strategy_id.")
    return get_strategy_definition(strategy_id)


@router.get("", response_model=StrategyCatalogResponse)
def get_strategies() -> StrategyCatalogResponse:
    return build_strategy_catalog()


@router.get("/{strategy_id}/latest", response_model=StrategyRunSnapshot)
def get_latest_strategy(strategy_id: str, data_as_of: date | None = None) -> StrategyRunSnapshot:
    _definition(strategy_id)
    repo = SQLiteStrategyRepository()
    latest = repo.latest(strategy_id) if data_as_of is None else None
    try:
        return latest or materialize_strategy_run(strategy_id, data_as_of=data_as_of, repository=repo)
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{strategy_id}/history", response_model=StrategyHistoryResponse)
def get_strategy_history(strategy_id: str, limit: int = Query(30, ge=1, le=120)) -> StrategyHistoryResponse:
    definition = _definition(strategy_id)
    return StrategyHistoryResponse(strategy=definition, runs=SQLiteStrategyRepository().list_runs(strategy_id, limit))


@router.get("/{strategy_id}/stocks/{symbol}", response_model=StrategyStockResponse)
def get_strategy_stock(strategy_id: str, symbol: str, data_as_of: date | None = None) -> StrategyStockResponse:
    definition = _definition(strategy_id)
    run = get_latest_strategy(strategy_id, data_as_of)
    candidate = SQLiteStrategyRepository().get_candidate(run.run_id, symbol)
    return StrategyStockResponse(
        strategy=definition,
        data_as_of=run.data_as_of,
        symbol=symbol,
        candidate=candidate,
        path=strategy_stock_path(strategy_id, symbol, data_as_of=run.data_as_of),
    )


@router.get("/{strategy_id}/statistics", response_model=StrategyStatisticsResponse)
def get_strategy_statistics(
    strategy_id: str,
    data_as_of: date | None = None,
    days: int = Query(7, ge=1, le=30),
) -> StrategyStatisticsResponse:
    definition = _definition(strategy_id)
    try:
        result = strategy_statistics(strategy_id, data_as_of=data_as_of, days=days)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return StrategyStatisticsResponse(strategy=definition, statistics=result)
