"""Stock detail API routes used by the review page."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from requests import RequestException

from app.collectors import (
    collect_stock_intraday_kline,
)
from app.collectors.stock_kline_collector import build_stock_close_snapshot
from app.models import (
    LimitUpEvent,
    StockCloseSnapshot,
    StockDetailMarketData,
    StockIntradayKLineBar,
    StockKLineBar,
    StockNewsFacts,
    StockPositionAssessment,
)
from app.repositories import (
    SQLiteFirstBoardRepository,
    SQLiteStockNewsRepository,
    get_limit_up_repository,
)
from app.services.analysis import find_stock_event, latest_trade_date
from app.services.stock_kline import (
    load_stock_detail_market_data,
    load_stock_kline_bars,
    load_stock_position_assessment,
)
from app.services.stock_news import collect_stock_news

router = APIRouter()


@router.get("/{symbol}/event", response_model=LimitUpEvent)
def get_stock_event(
    symbol: str,
    trade_date: date | None = None,
) -> LimitUpEvent:
    """Return a stock's requested limit-up event or its latest local event."""

    event = find_stock_event(
        get_limit_up_repository().list_events(),
        symbol=symbol,
        trade_date=trade_date,
    )
    if event is None:
        raise HTTPException(status_code=404, detail="stock limit-up event not found")
    return event


@router.get("/{symbol}/news", response_model=StockNewsFacts)
def get_stock_news(
    symbol: str,
    name: str | None = Query(default=None, max_length=40),
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=3, ge=1, le=10),
) -> StockNewsFacts:
    """Return a bounded recent-news list without blocking other detail facts."""

    events = get_limit_up_repository().list_events()
    event = find_stock_event(events, symbol=symbol)
    resolved_name = (name or (event.name if event else None) or symbol).strip()
    try:
        return collect_stock_news(
            symbol=symbol,
            name=resolved_name,
            days=days,
            limit=limit,
            repository=SQLiteStockNewsRepository(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/{symbol}/kline", response_model=list[StockKLineBar])
def get_stock_kline(
    symbol: str,
    days: int = Query(default=60, ge=1, le=60),
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


@router.get("/{symbol}/market-data", response_model=StockDetailMarketData)
def get_stock_market_data(
    symbol: str,
    days: int = Query(default=60, ge=1, le=60),
    position_trade_date: date | None = None,
) -> StockDetailMarketData:
    """Return one local-first bundle for the stock detail market panels."""

    try:
        events = get_limit_up_repository().list_events()
        return load_stock_detail_market_data(
            symbol=symbol,
            days=days,
            end_date=latest_trade_date(events),
            position_trade_date=position_trade_date,
            repository=SQLiteFirstBoardRepository(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="stock market data source unavailable",
        ) from error


@router.get("/{symbol}/position", response_model=StockPositionAssessment)
def get_stock_position(
    symbol: str,
    trade_date: date | None = None,
) -> StockPositionAssessment:
    """Return a K-line-based position assessment as of the requested close."""

    try:
        events = get_limit_up_repository().list_events()
        target_date = trade_date or latest_trade_date(events)
        return load_stock_position_assessment(
            symbol=symbol,
            end_date=target_date,
            repository=SQLiteFirstBoardRepository(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="stock position data source unavailable",
        ) from error



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
    trade_date: date | None = None,
) -> list[StockIntradayKLineBar]:
    """Return intraday K-line bars for a requested or latest persisted trade date."""

    try:
        events = get_limit_up_repository().list_events()
        return collect_stock_intraday_kline(
            symbol,
            trade_date=trade_date or latest_trade_date(events),
            period=period,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="stock trading day kline data source unavailable",
        ) from error

