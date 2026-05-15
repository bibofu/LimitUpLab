from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field


class LimitUpEvent(BaseModel):
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
    name: str
    symbol: str
    close: float
    change_pct: float
    trend: list[float]


class ConceptHeat(BaseModel):
    name: str
    limit_up_count: int
    failed_count: int


class MarketSummary(BaseModel):
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
    board_height: int
    sample_size: int
    continued_count: int
    probability: float


class FailedRateStat(BaseModel):
    board_height: int
    sample_size: int
    failed_count: int
    failed_rate: float


class PostPerformanceStat(BaseModel):
    board_height: int
    sample_size: int
    avg_next_open_pct: float
    avg_next_high_pct: float
    avg_next_close_pct: float
    avg_five_day_return_pct: float
