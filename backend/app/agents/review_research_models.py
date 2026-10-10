"""Typed evidence for candidate contrasts and market first-board follow-through."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class ReviewBucketStat(BaseModel):
    label: str
    positive_count: int
    negative_count: int
    sample_size: int
    positive_rate: float | None = None
    baseline_rate: float | None = None
    delta_pp: float | None = None


class ReviewFeatureBreakdown(BaseModel):
    key: str
    label: str
    positive_summary: str
    negative_summary: str
    positive_detail: str
    negative_detail: str
    positive_valid_count: int
    negative_valid_count: int
    buckets: list[ReviewBucketStat] = Field(default_factory=list)


class ReviewFeatureStudy(BaseModel):
    positive_count: int
    negative_count: int
    excluded_count: int
    basis: str
    baseline_rate: float | None = None
    features: list[ReviewFeatureBreakdown] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)


class ReviewLeaderExample(BaseModel):
    symbol: str
    name: str
    first_board_date: date | None = None
    max_board_height: int
    latest_date: date
    status: str
    data_missing: list[str] = Field(default_factory=list)
    position_label: str | None = None
    float_market_cap: float | None = None
    first_limit_time: str | None = None
    break_count: int | None = None
    turnover_rate: float | None = None


class ReviewFeatureResearch(BaseModel):
    candidate: ReviewFeatureStudy
    market: ReviewFeatureStudy
    market_start_date: date
    market_end_date: date
    market_leaders: list[ReviewLeaderExample] = Field(default_factory=list)
    market_detected_count: int = 0
    market_matched_count: int = 0
    notes: list[str] = Field(default_factory=list)


class ReviewInsight(BaseModel):
    scope: Literal["candidate", "market", "synthesis"]
    title: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=2, max_length=28)]
    detail: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=12, max_length=180)]
