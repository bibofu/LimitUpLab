"""Evidence for the four-part, cutoff-bound review summary."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DigestBucket(BaseModel):
    label: str
    count: int
    share: float | None = None
    baseline_count: int | None = None
    baseline_share: float | None = None


class DigestDistribution(BaseModel):
    key: str
    label: str
    valid_count: int
    total_count: int
    baseline_valid_count: int | None = None
    baseline_total_count: int | None = None
    buckets: list[DigestBucket] = Field(default_factory=list)
    note: str = ""


class DigestObservation(BaseModel):
    id: str
    dimension: str
    text: str
    support_count: int
    sample_size: int


class DigestStock(BaseModel):
    symbol: str
    name: str
    first_board_date: date | None = None
    observed_days: int | None = None
    return_pct: float | None = None
    first_close: float | None = None
    cutoff_close: float | None = None
    position_label: str | None = None
    industry: str | None = None
    concepts: list[str] = Field(default_factory=list)
    float_market_cap: float | None = None
    first_limit_time: str | None = None
    break_count: int | None = None
    turnover_rate: float | None = None
    max_board_height: int | None = None
    second_board_date: date | None = None
    second_open_pct: float | None = None
    second_limit_time: str | None = None
    second_break_count: int | None = None
    second_turnover_rate: float | None = None
    second_board_shape: str | None = None
    data_missing: list[str] = Field(default_factory=list)


class DigestGroup(BaseModel):
    scope: Literal["excellent", "weak", "leaders"]
    sample_size: int
    summary: str = ""
    observations: list[DigestObservation] = Field(default_factory=list)
    selected_observation_ids: list[str] = Field(default_factory=list)
    distributions: list[DigestDistribution] = Field(default_factory=list)
    stocks: list[DigestStock] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DigestOverview(BaseModel):
    headline: str
    candidate_count: int
    excellent_count: int
    weak_count: int
    ordinary_count: int
    unobserved_count: int
    comparable_days: int
    outperform_days: int
    best_date: date | None = None
    candidate_promoted: int = 0
    candidate_promotion_total: int = 0
    candidate_promotion_rate: float | None = None
    market_promoted: int = 0
    market_promotion_total: int = 0
    market_promotion_rate: float | None = None
    advantage_pp: float | None = None


class ReviewDigest(BaseModel):
    version: str = "four-part-v1"
    as_of_date: date
    candidate_dates: list[date] = Field(default_factory=list)
    market_dates: list[date] = Field(default_factory=list)
    overview: DigestOverview
    excellent: DigestGroup
    weak: DigestGroup
    leaders: DigestGroup
    notes: list[str] = Field(default_factory=list)
