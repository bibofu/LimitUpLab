"""Stock detail API routes used by the review page."""

from fastapi import APIRouter, HTTPException, Query
from requests import RequestException

from app.collectors import (
    collect_stock_intraday_kline,
)
from app.collectors.stock_kline_collector import build_stock_close_snapshot
from app.models import StockCloseSnapshot, StockIntradayKLineBar, StockKLineBar
from app.repositories import SQLiteFirstBoardRepository, get_limit_up_repository
from app.services.analysis import latest_trade_date
from app.services.stock_kline import load_stock_kline_bars

router = APIRouter()


@router.get("/{symbol}/kline", response_model=list[StockKLineBar])
def get_stock_kline(
    symbol: str,
    days: int = Query(default=5, ge=1, le=60),
) -> list[StockKLineBar]:
    """Return recent daily K-line bars ending at the latest persisted trade date."""

    try:
        events = get_limit_up_repository().list_events()
        return load_stock_kline_bars(
            symbol=symbol,
            days=days,
            end_date=latest_trade_date(events),
            repository=SQLiteFirstBoardRepository(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(status_code=502, detail="stock kline data source unavailable") from error



@router.get("/{symbol}/latest-close", response_model=StockCloseSnapshot)
def get_stock_latest_close(symbol: str) -> StockCloseSnapshot:
    """Return the latest available after-close daily price snapshot."""

    try:
        events = get_limit_up_repository().list_events()
        bars = load_stock_kline_bars(
            symbol=symbol,
            days=2,
            end_date=latest_trade_date(events),
            repository=SQLiteFirstBoardRepository(),
        )
        return build_stock_close_snapshot(
            symbol=symbol,
            bars=bars,
            source="local-first-kline",
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="stock close data source unavailable",
        ) from error
@router.get("/{symbol}/trading-day-kline", response_model=list[StockIntradayKLineBar])
def get_stock_trading_day_kline(
    symbol: str,
    period: int = Query(default=5, ge=1, le=60),
) -> list[StockIntradayKLineBar]:
    """Return after-close intraday K-line bars for the latest persisted trade date."""

    try:
        events = get_limit_up_repository().list_events()
        return collect_stock_intraday_kline(
            symbol,
            trade_date=latest_trade_date(events),
            period=period,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="stock trading day kline data source unavailable",
        ) from error

