from datetime import date, datetime, time
from typing import Any, Literal

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


class StockCloseSnapshot(BaseModel):
    """Latest available daily close snapshot for stock detail display."""

    symbol: str
    trade_date: date
    close: float
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float
    source: str


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

class FirstBoardFeature(BaseModel):
    """Persisted first-board feature row used for similar-case recall."""

    trade_date: date
    symbol: str
    name: str
    first_limit_minutes: int
    first_limit_bucket: str
    break_count: int
    seal_count: int
    turnover_rate: float
    turnover_bucket: str
    amount: float
    amount_log: float
    amount_bucket: str
    industry: str
    concept: str
    same_industry_limit_up_count: int
    same_concept_limit_up_count: int
    market_limit_up_count: int
    market_first_board_count: int
    market_failed_limit_up_rate: float
    market_failed_rate_bucket: str
    market_max_board_height: int
    market_sentiment: Literal["heating", "diverging", "cooling"]
    closed_limit: bool
    feature_version: str
    created_at: datetime


class StockDailyBar(BaseModel):
    """Persisted daily K-line bar used for post-first-board outcome review."""

    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    change_pct: float | None = None
    source: str
    created_at: datetime


class FirstBoardOutcome(BaseModel):
    """Derived post-first-board outcome summary for one historical case."""

    base_trade_date: date
    symbol: str
    next_trade_date: date | None = None
    next_open_pct: float | None = None
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    max_drawdown_3d: float | None = None
    promoted_to_second_board: bool
    outcome_ready: bool
    outcome_version: str
    created_at: datetime


class SimilarCaseOutcome(BaseModel):
    """Compact post-first-board outcome shown for one similar case."""

    next_trade_date: date | None = None
    next_open_pct: float | None = None
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    max_drawdown_3d: float | None = None
    promoted_to_second_board: bool
    outcome_ready: bool


class SimilarCaseDailyBar(BaseModel):
    """Daily bar displayed after a similar case's first-board date."""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


class SimilarFirstBoardCase(BaseModel):
    """One historical first-board case similar to the target stock."""

    symbol: str
    name: str
    trade_date: date
    similarity: float
    reasons: list[str]
    differences: list[str]
    outcome: SimilarCaseOutcome | None = None
    post_bars: list[SimilarCaseDailyBar] = Field(default_factory=list)


class SimilarFirstBoardCasesResponse(BaseModel):
    """Similar-case response for one target first-board stock."""

    target: FirstBoardFeature
    cases: list[SimilarFirstBoardCase]
    window_days: int
    recall_count: int
    generated_by: str


class FirstBoardFilterResult(BaseModel):
    """Candidate-pool filter audit for one latest-day first-board event."""

    symbol: str
    name: str
    included: bool
    excluded_reasons: list[str]
    data_missing: list[str]


class FirstBoardCandidateFacts(BaseModel):
    """Structured facts used to rate one first-board limit-up candidate."""

    symbol: str
    name: str
    trade_date: date
    first_limit_time: time
    last_limit_time: time
    seal_count: int
    break_count: int
    closed_limit: bool
    board_height: int
    amount: float
    turnover_rate: float
    industry: str
    concept: str
    same_industry_limit_up_count: int
    same_concept_limit_up_count: int
    market_limit_up_count: int
    market_first_board_count: int
    market_failed_limit_up_rate: float
    market_max_board_height: int
    market_sentiment: Literal["heating", "diverging", "cooling"]
    data_missing: list[str] = Field(default_factory=list)


class ScoreBreakdownItem(BaseModel):
    """One explainable scoring dimension in the first-board rating engine."""

    name: str
    score: float
    max_score: float
    evidence: list[str]


class FirstBoardRating(BaseModel):
    """Explainable first-board quality rating for one candidate."""

    facts: FirstBoardCandidateFacts
    score: float
    rating: Literal["A", "B", "C", "D"]
    confidence: float
    score_breakdown: list[ScoreBreakdownItem]
    reasons: list[str]
    risks: list[str]


class FirstBoardRatingsResponse(BaseModel):
    """API response for a trade date's first-board candidate ratings."""

    trade_date: date
    candidates: list[FirstBoardRating]
    filtered_out: list[FirstBoardFilterResult]
    universe_count: int
    generated_by: str


class AgentChatRequest(BaseModel):
    """User chat request with optional page context."""

    session_id: str
    message: str
    intent_hint: str | None = None
    trade_date: date | None = None
    symbol: str | None = None
    page_context: dict[str, str] = Field(default_factory=dict)


class AgentChatResponse(BaseModel):
    """Tool-grounded chat response from the first-board Agent."""

    session_id: str
    run_id: str | None = None
    intent: str
    answer: str
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list["AgentToolTrace"] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class AgentToolTrace(BaseModel):
    """Compact trace for one tool execution inside an Agent run."""

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    summary: str


class AgentRun(BaseModel):
    """Persisted trace for one Agent execution."""

    run_id: str
    session_id: str
    run_type: str
    status: Literal["success", "error"]
    intent: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime


