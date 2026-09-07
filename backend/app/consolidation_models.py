"""Public evidence contract for the post-limit consolidation observation pool."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConsolidationCandidate(BaseModel):
    symbol: str
    name: str
    anchor_date: date
    confirmed_date: date
    state: Literal["new", "watching"]
    consolidation_days: int
    anchor_close: float
    close: float
    range_low: float
    range_high: float
    range_pct: float
    anchor_change_pct: float
    volume_ratio: float
    source: str
    reasons: list[str]
    risks: list[str]


class ConsolidationPool(BaseModel):
    strategy_version: str = "consolidation_research_v0.1"
    generated_at: datetime
    data_as_of: date | None = None
    latest_data_date: date | None = None
    available_dates: list[date] = Field(default_factory=list)
    status: Literal["ready", "empty", "data_missing"] = "data_missing"
    snapshot_kind: Literal["recomputed_observation"] = "recomputed_observation"
    calendar_source: str = "local_daily_bars_observed_dates"
    pool_count: int = 0
    evaluated_count: int = 0
    candidates: list[ConsolidationCandidate] = Field(default_factory=list)
    exclusions: dict[str, int] = Field(default_factory=dict)
    data_missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
