"""Public contracts for the post-limit strategy platform."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StrategyMaturity = Literal["exploratory", "forward_validation", "validated"]
StrategyOutputType = Literal["ranked_research", "observation_pool"]


class StrategyDefinition(BaseModel):
    strategy_id: str
    name: str
    description: str
    version: str
    lifecycle_stage: str
    maturity: StrategyMaturity
    output_type: StrategyOutputType
    universe: str
    cutoff_contract: str
    required_data: list[str]
    missing_data_policy: str
    outcome_metrics: list[str]
    promotion_gate: str
    latest_data_date: date | None = None
    latest_sample_size: int = 0


class StrategyCatalogResponse(BaseModel):
    strategies: list[StrategyDefinition]
    generated_at: datetime
    generated_by: str = "strategy-catalog-v1"


class StrategyRunSnapshot(BaseModel):
    run_id: str
    strategy_id: str
    strategy_version: str
    signal_date: date
    data_as_of: date
    generated_at: datetime
    input_fingerprint: str
    status: Literal["ready", "empty", "data_missing"]
    maturity: StrategyMaturity
    output_type: StrategyOutputType
    candidate_count: int
    payload: dict[str, Any] = Field(default_factory=dict)


class StrategyHistoryResponse(BaseModel):
    strategy: StrategyDefinition
    runs: list[StrategyRunSnapshot]
    generated_by: str = "strategy-history-v1"


class StrategyStockResponse(BaseModel):
    strategy: StrategyDefinition
    data_as_of: date
    symbol: str
    candidate: dict[str, Any] | None = None
    path: dict[str, Any]
    generated_by: str = "strategy-stock-detail-v1"


class StrategyStatisticsResponse(BaseModel):
    strategy: StrategyDefinition
    statistics: dict[str, Any]
    generated_by: str = "strategy-statistics-v1"
