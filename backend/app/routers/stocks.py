"""Stock detail API routes used by the review page."""

from fastapi import APIRouter, HTTPException, Query
from requests import RequestException

from app.collectors import collect_stock_intraday_kline, collect_stock_kline
from app.models import StockIntradayKLineBar, StockKLineBar
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date

router = APIRouter()


@router.get("/{symbol}/kline", response_model=list[StockKLineBar])
def get_stock_kline(
    symbol: str,
    days: int = Query(default=5, ge=1, le=30),
) -> list[StockKLineBar]:
    """Return recent daily K-line bars ending at the latest persisted trade date."""

    try:
        events = get_limit_up_repository().list_events()
        return collect_stock_kline(
            symbol,
            days=days,
            end_date=latest_trade_date(events),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(status_code=502, detail="stock kline data source unavailable") from error


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
