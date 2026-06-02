from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field


class LimitUpEvent(BaseModel):
    """One stock's daily limit-up or failed limit-up event."""

    symbol: str = Field(examples=["600519"])
    name: str = Field(examples=["贵州茅台"])
    trade_date: date
    first_limit_time: time
    last_limit_time: time
    seal_count: int
    break_count: int
    closed_limit: bool
    board_height: int
    amount: float = Field(description="Turnover amount in CNY.")
    turnover_rate: float = Field(description="Turnover rate percentage.")
    industry: str
    concept: str
    next_open_pct: float
    next_high_pct: float
    next_close_pct: float
    three_day_return_pct: float
    five_day_return_pct: float
    continued_next_day: bool


class MarketIndexSnapshot(BaseModel):
    """Compact index snapshot used by the dashboard."""

    name: str
    symbol: str
    close: float
    change_pct: float
    trend: list[float]


class StockKLineBar(BaseModel):
    """Daily OHLCV bar for stock detail review."""

    trade_date: date
    open: float
    close: float
    high: float
    low: float
    volume: float


class StockIntradayKLineBar(BaseModel):
    """Intraday OHLCV bar for after-close trading-day review."""

    timestamp: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float


class ConceptHeat(BaseModel):
    """Limit-up and failed-count summary for one concept or topic."""

    name: str
    limit_up_count: int
    failed_count: int


class MarketSummary(BaseModel):
    """Dashboard summary for the latest persisted trading day."""

    trade_date: date
    limit_up_count: int
    first_board_count: int
    continued_board_count: int
    failed_count: int
    limit_down_count: int
    failed_limit_up_rate: float
    max_board_height: int
    total_amount: float
    hot_industries: list[str]
    hot_concepts: list[ConceptHeat]
    indices: list[MarketIndexSnapshot]
    sentiment: Literal["heating", "diverging", "cooling"]


class ContinuationStat(BaseModel):
    """Continuation probability bucket grouped by board height."""

    board_height: int
    sample_size: int
    continued_count: int
    probability: float


class FailedRateStat(BaseModel):
    """Intraday break-rate bucket grouped by board height."""

    board_height: int
    sample_size: int
    failed_count: int
    failed_rate: float


class PostPerformanceStat(BaseModel):
    """Average post-limit-up performance bucket grouped by board height."""

    board_height: int
    sample_size: int
    avg_next_open_pct: float
    avg_next_high_pct: float
    avg_next_close_pct: float
    avg_five_day_return_pct: float


class BoardLadderItem(BaseModel):
    """Count of latest-day events at one board height."""

    board_height: int
    count: int


class DailyReviewFacts(BaseModel):
    """Structured facts used by rule-based and future LLM daily reviews."""

    trade_date: date
    sentiment: Literal["heating", "diverging", "cooling"]
    limit_up_count: int
    first_board_count: int
    continued_board_count: int
    unstable_count: int
    unclosed_count: int
    failed_limit_up_rate: float
    max_board_height: int
    total_amount: float
    hot_industries: list[str]
    board_ladder: list[BoardLadderItem]
    risk_signals: list[str]


class DailyReview(BaseModel):
    """Rule-based daily market review response."""

    facts: DailyReviewFacts
    narrative: str
