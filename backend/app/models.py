from datetime import date, datetime, time
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    trade_date: date
    close: float
    change_pct: float
    trend: list[float]
    source: str


class SectorHistoryPoint(BaseModel):
    """One completed daily point for an industry sector."""

    trade_date: date
    close: float
    change_pct: float | None = None


class SectorRankingItem(BaseModel):
    """Compact sector ranking row from the latest market snapshot."""

    sector_name: str
    rank: int
    change_pct: float
    amount_yi: float | None = None
    up_count: int | None = None
    down_count: int | None = None
    leader_name: str | None = None
    leader_change_pct: float | None = None


class SectorPerformanceFacts(BaseModel):
    """Tool-ready industry performance, breadth, ranking and recent trend facts."""

    requested_sector: str | None = None
    sector_name: str | None = None
    trade_date: date
    data_as_of: date
    data_fresh: bool
    rank: int | None = None
    sector_count: int
    change_pct: float | None = None
    amount_yi: float | None = None
    net_inflow_yi: float | None = None
    up_count: int | None = None
    down_count: int | None = None
    leader_name: str | None = None
    leader_price: float | None = None
    leader_change_pct: float | None = None
    return_5d_pct: float | None = None
    return_20d_pct: float | None = None
    top_sectors: list[SectorRankingItem] = Field(default_factory=list)
    bottom_sectors: list[SectorRankingItem] = Field(default_factory=list)
    history: list[SectorHistoryPoint] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class WebSearchResult(BaseModel):
    """One sanitized result returned by the generic web-search tool."""

    title: str
    url: str
    domain: str
    snippet: str


class WebSearchFacts(BaseModel):
    """Search query, retrieval timestamp and evidence snippets for the Agent."""

    query: str
    fetched_at: datetime
    provider: str
    results: list[WebSearchResult] = Field(default_factory=list)


class StockKLineBar(BaseModel):
    """Daily OHLCV bar for stock detail review."""

    trade_date: date
    open: float
    close: float
    high: float
    low: float
    volume: float


class StockKLineFacts(BaseModel):
    """Tool-ready K-line facts and derived trend indicators for one stock."""

    symbol: str
    requested_days: int
    requested_end_date: date
    data_as_of: date
    data_fresh: bool
    trend: Literal["rising", "falling", "oscillating", "insufficient"]
    latest_close: float
    return_5d_pct: float | None = None
    return_10d_pct: float | None = None
    return_20d_pct: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    volume_ratio_5d: float | None = None
    max_drawdown_pct: float | None = None
    sources: list[str] = Field(default_factory=list)
    bars: list[StockKLineBar] = Field(default_factory=list)


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
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    max_drawdown_3d: float | None = None
    three_day_open_to_high_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    promoted_to_second_board: bool
    next_day_ready: bool = False
    three_day_ready: bool = False
    outcome_ready: bool
    outcome_version: str
    created_at: datetime


class SimilarCaseOutcome(BaseModel):
    """Compact post-first-board outcome shown for one similar case."""

    next_trade_date: date | None = None
    next_open_pct: float | None = None
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    max_drawdown_3d: float | None = None
    three_day_open_to_high_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    promoted_to_second_board: bool
    next_day_ready: bool = False
    three_day_ready: bool = False
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


class FirstBoardEnrichmentSnapshot(BaseModel):
    """Point-in-time enrichment facts available after the first-board close."""

    trade_date: date
    symbol: str
    kline_bar_count: int = 0
    return_5d_pct: float | None = None
    return_20d_pct: float | None = None
    return_60d_pct: float | None = None
    distance_20d_high_pct: float | None = None
    distance_60d_high_pct: float | None = None
    volume_ratio_5d: float | None = None
    volatility_20d: float | None = None
    close_above_ma20: bool | None = None
    ma_alignment: str = "unknown"
    listing_date: date | None = None
    listing_age_days: int | None = None
    float_market_cap: float | None = None
    float_market_cap_source: str | None = None
    recent_limit_up_count_20d: int = 0
    recent_limit_up_count_60d: int = 0
    industry_first_board_count: int = 0
    industry_continued_board_count: int = 0
    industry_failed_count: int = 0
    industry_max_board_height: int = 0
    industry_first_limit_rank: int | None = None
    previous_first_board_promotion_rate: float | None = None
    market_first_board_seal_rate: float | None = None
    dragon_tiger_on_list: bool = False
    dragon_tiger_net_buy_amount: float | None = None
    dragon_tiger_buy_amount: float | None = None
    dragon_tiger_sell_amount: float | None = None
    dragon_tiger_reason: str | None = None
    dragon_tiger_source: str | None = None
    popularity_rank: int | None = None
    popularity_rank_change: int | None = None
    popularity_snapshot_at: datetime | None = None
    popularity_source: str | None = None
    data_missing: list[str] = Field(default_factory=list)
    feature_version: str
    created_at: datetime


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
    enrichment: FirstBoardEnrichmentSnapshot | None = None
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


class ScoringPolicy(BaseModel):
    """Versioned first-board scoring weights and promotion metadata."""

    version: str
    parent_version: str | None = None
    status: Literal["champion", "challenger", "archived"]
    factor_weights: dict[str, float]
    source: Literal["default", "optimizer", "manual"] = "default"
    rationale: list[str] = Field(default_factory=list)
    training_start_date: date | None = None
    training_end_date: date | None = None
    created_at: datetime
    activated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_weights(self) -> "ScoringPolicy":
        """Require finite non-negative weights whose total is 100 points."""

        if not self.factor_weights:
            raise ValueError("factor_weights cannot be empty")
        if any(
            not isfinite(value) or value < 0
            for value in self.factor_weights.values()
        ):
            raise ValueError("factor weights must be finite and non-negative")
        if abs(sum(self.factor_weights.values()) - 100.0) > 0.05:
            raise ValueError("factor weights must sum to 100")
        return self


class ScoringPolicyMetrics(BaseModel):
    """Out-of-sample Top-K ranking metrics for one scoring policy."""

    policy_version: str
    trade_date_count: int
    pool_sample_size: int
    top_sample_size: int
    top_k: int
    avg_next_open_to_close_pct: float | None = None
    positive_rate: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    promoted_to_second_board_rate: float | None = None
    large_loss_rate: float | None = None
    avg_next_open_to_low_pct: float | None = None
    pool_avg_next_open_to_close_pct: float | None = None
    excess_next_open_to_close_pct: float | None = None
    objective_score: float | None = None


class ScoringPolicyComparison(BaseModel):
    """Champion-versus-challenger validation and promotion decision."""

    champion: ScoringPolicyMetrics
    challenger: ScoringPolicyMetrics
    objective_delta: float | None = None
    positive_rate_delta: float | None = None
    drawdown_delta: float | None = None
    promotion_rate_delta: float | None = None
    large_loss_rate_delta: float | None = None
    promotion_eligible: bool
    gate_reasons: list[str] = Field(default_factory=list)


class ScoringWalkForwardFold(BaseModel):
    """One expanding-window validation and out-of-sample test fold."""

    fold_index: int
    train_dates: list[date]
    validation_dates: list[date]
    test_dates: list[date]
    selected_strength: float
    champion_metrics: ScoringPolicyMetrics
    challenger_metrics: ScoringPolicyMetrics


class ScoringPolicyOptimizationResponse(BaseModel):
    """One constrained scoring-policy optimization run."""

    run_id: str
    champion_policy: ScoringPolicy
    challenger_policy: ScoringPolicy
    train_dates: list[date]
    validation_dates: list[date]
    test_dates: list[date]
    factor_correlations: dict[str, float]
    target_correlations: dict[str, dict[str, float]] = Field(default_factory=dict)
    target_weights: dict[str, float] = Field(default_factory=dict)
    walk_forward_folds: list[ScoringWalkForwardFold] = Field(default_factory=list)
    comparison: ScoringPolicyComparison
    activated: bool = False
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class ScoringPolicyRegistryResponse(BaseModel):
    """Current champion and recent registered scoring policies."""

    champion: ScoringPolicy
    policies: list[ScoringPolicy]
    generated_by: str


class PredictionQualityCohort(BaseModel):
    """Prediction coverage for one source or scoring-version cohort."""

    dimension: Literal["prediction_source", "scoring_version"]
    value: str
    row_count: int
    unique_stock_date_count: int
    trade_date_count: int
    next_day_ready_count: int
    next_day_coverage_rate: float


class PredictionDateCoverage(BaseModel):
    """Outcome maturity and cache coverage for one prediction date."""

    trade_date: date
    candidate_count: int
    top_count: int
    next_day_ready_count: int
    three_day_ready_count: int
    next_day_coverage_rate: float
    three_day_coverage_rate: float
    next_day_mature: bool
    three_day_mature: bool
    status: Literal["complete", "partial", "pending", "not_mature"]


class PredictionBenchmarkMetrics(BaseModel):
    """Comparable performance metrics for one deterministic ranking baseline."""

    benchmark: str
    label: str
    trade_date_count: int
    sample_size: int
    avg_next_open_to_close_pct: float | None = None
    positive_rate: float | None = None
    promoted_to_second_board_rate: float | None = None
    large_loss_rate: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    excess_vs_ready_pool_pct: float | None = None


class PredictionQualityPolicyStatus(BaseModel):
    """Current policy-governance readiness summarized for the audit panel."""

    champion_version: str
    latest_challenger_version: str | None = None
    latest_optimizer_version: str | None = None
    promotion_eligible: bool | None = None
    outcome_ready_trade_dates: int
    required_trade_dates: int
    readiness_rate: float
    gate_reasons: list[str] = Field(default_factory=list)


class PredictionQualityAuditResponse(BaseModel):
    """Auditable prediction coverage, benchmark and policy-readiness report."""

    start_date: date
    end_date: date
    latest_trade_date: date
    audited_scoring_version: str
    top_k: int
    raw_prediction_rows: int
    audited_prediction_rows: int
    canonical_prediction_count: int
    cross_cohort_duplicate_rows: int
    data_as_of_violation_count: int
    prediction_trade_date_count: int
    next_day_mature_trade_date_count: int
    complete_next_day_trade_date_count: int
    next_day_outcome_coverage_rate: float
    three_day_outcome_coverage_rate: float
    cohorts: list[PredictionQualityCohort]
    date_coverage: list[PredictionDateCoverage]
    benchmarks: list[PredictionBenchmarkMetrics]
    policy_status: PredictionQualityPolicyStatus
    findings: list[str]
    recommendations: list[str]
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class RatingBacktestBucket(BaseModel):
    """Aggregated post-board outcome metrics for one rating bucket."""

    rating: str
    sample_size: int
    outcome_ready_count: int
    avg_next_open_pct: float | None = None
    avg_next_high_pct: float | None = None
    avg_next_close_pct: float | None = None
    avg_next_open_to_high_pct: float | None = None
    avg_next_open_to_close_pct: float | None = None
    avg_next_open_to_low_pct: float | None = None
    avg_three_day_high_pct: float | None = None
    avg_three_day_close_pct: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    next_open_to_close_positive_rate: float | None = None
    next_open_to_close_large_loss_rate: float | None = None
    promoted_to_second_board_rate: float | None = None


class RatingBacktestFailureSample(BaseModel):
    """High-rated candidate whose post-board outcome was weak."""

    symbol: str
    name: str
    trade_date: date
    rating: str
    score: float
    next_close_pct: float | None = None
    next_open_to_close_pct: float | None = None
    next_open_to_low_pct: float | None = None
    three_day_close_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    promoted_to_second_board: bool
    reasons: list[str]
    risks: list[str]


class RatingBacktestResponse(BaseModel):
    """Self-evaluation summary for first-board ratings over a date range."""

    start_date: date
    end_date: date
    trade_dates: list[date]
    sample_size: int
    outcome_ready_count: int
    buckets: list[RatingBacktestBucket]
    failure_samples: list[RatingBacktestFailureSample]
    observations: list[str]
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class FirstBoardCriticResponse(BaseModel):
    """Critic review for one first-board rating without changing the score."""

    symbol: str
    name: str
    trade_date: date
    rating: str
    score: float
    original_confidence: float
    suggested_confidence: float
    confidence_delta: float
    verdict: Literal["supportive", "cautious", "fragile"]
    support_evidence: list[str]
    counter_evidence: list[str]
    missing_data: list[str]
    critic_warnings: list[str]
    review_questions: list[str]
    similar_case_count: int
    similar_case_outcome_ready_count: int
    generated_by: str


class AgentPrediction(BaseModel):
    """Persisted first-board rating snapshot for later evaluation."""

    prediction_id: str
    trade_date: date
    symbol: str
    name: str
    score: float
    rating: str
    confidence: float
    scoring_version: str
    prediction_source: Literal["live", "historical_backtest"]
    data_as_of: date
    facts_json: dict[str, Any]
    reasons: list[str]
    risks: list[str]
    created_at: datetime


class AgentEvaluationItem(BaseModel):
    """Post-outcome evaluation for one persisted first-board prediction."""

    prediction_id: str
    trade_date: date
    symbol: str
    name: str
    score: float
    rating: str
    confidence: float
    prediction_source: Literal["live", "historical_backtest"]
    data_as_of: date
    evaluation_label: Literal[
        "success",
        "partial",
        "miss",
        "avoid_success",
        "false_negative",
        "pending",
    ]
    outcome_ready: bool
    promoted_to_second_board: bool
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    lesson: str
    scoring_suggestion: str


class AgentEvaluationResponse(BaseModel):
    """Evaluation Agent summary for persisted first-board predictions."""

    start_date: date
    end_date: date
    prediction_count: int
    outcome_ready_count: int
    source_counts: dict[str, int]
    label_counts: dict[str, int]
    evaluations: list[AgentEvaluationItem]
    summary: list[str]
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class ReviewAgentPick(BaseModel):
    """One high-score first-board pick reviewed by the Review Agent."""

    trade_date: date
    symbol: str
    name: str
    score: float
    rating: str
    confidence: float
    prediction_source: Literal["live", "historical_backtest"]
    data_as_of: date
    evaluation_label: str
    outcome_ready: bool
    promoted_to_second_board: bool
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    post_bars: list["ReviewAgentPostBar"] = Field(default_factory=list)
    expected_post_bar_count: int = 0
    post_bar_cache_complete: bool = False


class ReviewAgentPostBar(BaseModel):
    """One cached daily bar after a reviewed first-board pick."""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    change_pct: float | None = None
    return_from_base_pct: float | None = None


class ReviewAgentReportResponse(BaseModel):
    """LLM tool-driven review report for high-score first-board picks."""

    start_date: date
    end_date: date
    sample_size: int
    success_count: int
    failed_count: int
    pending_count: int
    main_findings: list[str] = Field(default_factory=list)
    successful_patterns: list[str] = Field(default_factory=list)
    failed_patterns: list[str] = Field(default_factory=list)
    scoring_bias: list[str] = Field(default_factory=list)
    adjustment_suggestions: list[str] = Field(default_factory=list)
    confidence: float
    reviewed_picks: list[ReviewAgentPick] = Field(default_factory=list)
    tool_results: list["AgentToolTrace"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class AgentEvalCaseReport(BaseModel):
    """One deterministic chat eval case shown in the frontend quality panel."""

    case_id: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    intent: str
    planner_tool_calls: list[str] = Field(default_factory=list)
    final_tool_calls: list[str] = Field(default_factory=list)
    backend_repaired_tools: list[str] = Field(default_factory=list)
    repair_reasons: list[str] = Field(default_factory=list)
    trace_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    answer_preview: str


class AgentEvalReportResponse(BaseModel):
    """Deterministic Agent regression suite report for local quality checks."""

    mode: Literal["offline"]
    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[AgentEvalCaseReport]
    generated_by: str


class AgentChatRequest(BaseModel):
    """User chat request with optional page context."""

    session_id: str
    message: str
    intent_hint: str | None = None
    trade_date: date | None = None
    symbol: str | None = None
    page_context: dict[str, str] = Field(default_factory=dict)


class AgentChatPerformance(BaseModel):
    """Measured latency and prompt sizes for one LLM-backed chat turn."""

    planner_duration_ms: int = 0
    tool_duration_ms: int = 0
    answer_duration_ms: int = 0
    total_duration_ms: int = 0
    planner_prompt_chars: int = 0
    answer_prompt_chars: int = 0


class AgentEvidenceCard(BaseModel):
    """User-facing evidence extracted from raw Agent tool traces."""

    title: str
    kind: Literal[
        "execution",
        "market",
        "candidate_pool",
        "limit_up_events",
        "rating",
        "critic",
        "similar_cases",
        "evaluation",
        "data_availability",
        "tool",
    ]
    status: Literal["success", "error", "skipped"] = "success"
    summary: str
    facts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_tools: list[str] = Field(default_factory=list)


class AgentToolPolicyAudit(BaseModel):
    """Planner-vs-final audit for one Agent response."""

    planner_tool_calls: list[str] = Field(default_factory=list)
    final_tool_calls: list[str] = Field(default_factory=list)
    backend_repaired_tools: list[str] = Field(default_factory=list)
    repair_reasons: list[str] = Field(default_factory=list)
    safety_fallback_used: bool = False


class AgentChatResponse(BaseModel):
    """Tool-grounded chat response from the first-board Agent."""

    session_id: str
    run_id: str | None = None
    intent: str
    answer: str
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list["AgentToolTrace"] = Field(default_factory=list)
    evidence_cards: list[AgentEvidenceCard] = Field(default_factory=list)
    tool_policy: AgentToolPolicyAudit = Field(default_factory=AgentToolPolicyAudit)
    references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    performance: AgentChatPerformance = Field(default_factory=AgentChatPerformance)
    generated_by: str

    @model_validator(mode="after")
    def populate_agent_ui_fields(self) -> "AgentChatResponse":
        """Build UI evidence and planner audit fields when callers omit them."""

        if not self.evidence_cards:
            self.evidence_cards = build_agent_evidence_cards(self.tool_results, self.warnings)
        if not self.tool_policy.final_tool_calls:
            self.tool_policy = build_agent_tool_policy_audit(
                tool_calls=self.tool_calls,
                tool_results=self.tool_results,
                warnings=self.warnings,
            )
        return self


class AgentToolTrace(BaseModel):
    """Compact trace for one tool execution inside an Agent run."""

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    summary: str
    status: Literal["success", "error", "skipped"] = "success"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None


def build_agent_tool_policy_audit(
    *,
    tool_calls: list[str],
    tool_results: list[AgentToolTrace],
    warnings: list[str] | None = None,
) -> AgentToolPolicyAudit:
    """Compare the LLM planner's requested tools with final backend execution."""

    planner_calls = _extract_planner_tool_calls(tool_results)
    planner_trace_present = any(
        trace.name == "llm_tool_planner" for trace in tool_results
    )
    final_calls = [
        tool
        for tool in tool_calls
        if tool not in {"llm_tool_planner", "llm_tool_answer", "template_general_answer"}
    ]
    if not final_calls:
        final_calls = [
            trace.name
            for trace in tool_results
            if trace.name not in {"llm_tool_planner", "llm_tool_answer", "template_general_answer"}
        ]
    backend_repaired = [
        tool
        for tool in final_calls
        if planner_trace_present and tool not in planner_calls
    ]
    warnings = warnings or []
    return AgentToolPolicyAudit(
        planner_tool_calls=planner_calls,
        final_tool_calls=final_calls,
        backend_repaired_tools=backend_repaired,
        repair_reasons=[
            _repair_reason(tool, tool_results) for tool in backend_repaired
        ],
        safety_fallback_used=any(
            "template fallback" in warning.lower()
            or "safety" in warning.lower()
            or "disabled" in warning.lower()
            for warning in warnings
        ),
    )


def build_agent_evidence_cards(
    tool_results: list[AgentToolTrace],
    warnings: list[str] | None = None,
) -> list[AgentEvidenceCard]:
    """Convert raw tool traces into user-facing evidence cards."""

    if not tool_results and not warnings:
        return []

    cards: list[AgentEvidenceCard] = []
    if tool_results:
        policy = build_agent_tool_policy_audit(
            tool_calls=[],
            tool_results=tool_results,
            warnings=warnings,
        )
        success_count = sum(1 for trace in tool_results if trace.status == "success")
        error_count = sum(1 for trace in tool_results if trace.status == "error")
        skipped_count = sum(1 for trace in tool_results if trace.status == "skipped")
        cards.append(
            AgentEvidenceCard(
                title="Agent 执行摘要",
                kind="execution",
                status="error" if error_count else "success",
                summary=f"本次回答调用 {len(tool_results)} 个工具，其中 {success_count} 个成功。",
                facts=_compact_texts([trace.summary for trace in tool_results[:4]]),
                metrics={
                    "tool_count": len(tool_results),
                    "success_count": success_count,
                    "error_count": error_count,
                    "skipped_count": skipped_count,
                    "repair_count": len(policy.backend_repaired_tools),
                },
                source_tools=[trace.name for trace in tool_results],
            )
        )
        if policy.planner_tool_calls or policy.backend_repaired_tools:
            cards.append(
                AgentEvidenceCard(
                    title="Planner vs Final",
                    kind="execution",
                    status="skipped" if policy.backend_repaired_tools else "success",
                    summary=(
                        "后端对 LLM 工具计划进行了补救。"
                        if policy.backend_repaired_tools
                        else "LLM 工具计划与最终执行一致。"
                    ),
                    facts=[
                        f"Planner: {', '.join(policy.planner_tool_calls) or '无'}",
                        f"Final: {', '.join(policy.final_tool_calls) or '无'}",
                        *policy.repair_reasons,
                    ],
                    metrics={"repair_count": len(policy.backend_repaired_tools)},
                    source_tools=["llm_tool_planner"],
                )
            )

    for trace in tool_results:
        card = _evidence_card_from_trace(trace)
        if card is not None:
            cards.append(card)

    if warnings:
        cards.append(
            AgentEvidenceCard(
                title="数据与回答限制",
                kind="data_availability",
                status="skipped",
                summary="本次回答存在需要注意的数据限制。",
                facts=_compact_texts(warnings, limit=5),
                source_tools=[],
            )
        )
    return cards


def _evidence_card_from_trace(trace: AgentToolTrace) -> AgentEvidenceCard | None:
    """Create one evidence card from a trace."""

    output = trace.output or {}
    metrics = _evidence_metrics(output)
    facts = _evidence_facts(trace, output)
    title, kind = _evidence_title_kind(trace.name)

    if trace.name in {"llm_tool_answer", "template_general_answer"}:
        return None

    return AgentEvidenceCard(
        title=title,
        kind=kind,
        status=trace.status,
        summary=trace.error or trace.summary,
        facts=facts,
        metrics=metrics,
        source_tools=[trace.name],
    )


def _evidence_title_kind(tool_name: str) -> tuple[str, str]:
    """Map internal tool names to UI evidence categories."""

    mapping: dict[str, tuple[str, str]] = {
        "agent_plan": ("问题理解与工具计划", "execution"),
        "llm_tool_planner": ("LLM 工具规划", "execution"),
        "market_summary": ("市场环境事实", "market"),
        "sector_performance": ("行业板块行情", "market"),
        "hot_stock_ranking": ("同花顺热股榜", "market"),
        "dragon_tiger_list": ("同花顺龙虎榜", "market"),
        "remote_limit_up_pool": ("同花顺涨停池", "limit_up_events"),
        "web_search": ("公开网络检索", "tool"),
        "first_board_ratings": ("首板候选池与评分", "candidate_pool"),
        "limit_up_events": ("涨停事件查询", "limit_up_events"),
        "first_board_filter": ("首板条件筛选", "candidate_pool"),
        "first_board_critic": ("评分反证与风险", "critic"),
        "first_board_similar_cases": ("历史相似案例", "similar_cases"),
        "rating_backtest": ("评分历史回测", "evaluation"),
        "rating_evaluation": ("Agent 自我评价", "evaluation"),
        "scoring_policy_status": ("评分策略迭代", "evaluation"),
        "limit_up_event_dates": ("本地数据日期", "data_availability"),
    }
    return mapping.get(tool_name, (tool_name, "tool"))


def _extract_planner_tool_calls(tool_results: list[AgentToolTrace]) -> list[str]:
    """Extract tool names from the LLM planner trace."""

    for trace in tool_results:
        if trace.name != "llm_tool_planner":
            continue
        raw_calls = trace.input.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            return []
        names: list[str] = []
        for raw_call in raw_calls:
            if isinstance(raw_call, dict) and raw_call.get("name"):
                names.append(str(raw_call["name"]))
        return names
    return []


def _repair_reason(
    tool_name: str,
    tool_results: list[AgentToolTrace],
) -> str:
    """Explain why the backend inserted a missing tool."""

    for trace in reversed(tool_results):
        if trace.name != tool_name:
            continue
        policy = trace.output.get("policy_repair")
        if isinstance(policy, dict) and policy.get("reason"):
            return str(policy["reason"])

    reasons = {
        "first_board_ratings": "用户问题需要评分或候选池事实，planner 未覆盖，后端补充 first_board_ratings。",
        "first_board_critic": "用户要求反证、风险或可靠性检查，后端补充 first_board_critic。",
        "first_board_similar_cases": "用户询问历史相似案例，后端补充 first_board_similar_cases。",
        "limit_up_event_dates": "用户询问本地是否有某日数据，后端补充 limit_up_event_dates。",
        "limit_up_events": "用户询问当天涨停/连板/炸板明细，后端补充 limit_up_events。",
        "sector_performance": "用户询问整个行业板块表现，后端补充 sector_performance。",
        "web_search": "用户询问最新外部信息，后端补充 web_search。",
        "rating_backtest": "用户询问评分效果或回测，后端补充 rating_backtest。",
        "rating_evaluation": "用户询问模型复盘或错判样本，后端补充 rating_evaluation。",
        "scoring_policy_status": "用户询问评分策略或权重迭代，后端补充 scoring_policy_status。",
    }
    return reasons.get(tool_name, f"后端补充 {tool_name} 以满足问题所需事实。")


def _evidence_metrics(output: dict[str, Any]) -> dict[str, Any]:
    """Extract displayable numeric/string metrics from trace output."""

    allowed = (
        "trade_date",
        "data_as_of",
        "captured_at",
        "period",
        "source",
        "sector_name",
        "sector_count",
        "rank",
        "change_pct",
        "up_count",
        "down_count",
        "candidate_count",
        "matched_count",
        "upstream_total",
        "stock_count",
        "event_count",
        "first_board_count",
        "continued_board_count",
        "failed_count",
        "limit_up_count",
        "recall_count",
        "case_count",
        "outcome_ready_count",
        "universe_count",
        "score",
        "rating",
        "confidence",
        "verdict",
        "status",
        "champion_version",
        "challenger_count",
        "latest_challenger",
        "promotion_eligible",
        "activated",
    )
    metrics: dict[str, Any] = {}
    for key in allowed:
        value = output.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            metrics[key] = value
    return {key: value for key, value in metrics.items() if value is not None}


def _evidence_facts(trace: AgentToolTrace, output: dict[str, Any]) -> list[str]:
    """Extract short factual bullets from common trace output fields."""

    raw: list[Any] = [trace.summary]
    for key in (
        "top_candidates",
        "matches",
        "events",
        "items",
        "cases",
        "support_evidence",
        "counter_evidence",
        "warnings",
        "available_dates",
        "reason",
    ):
        value = output.get(key)
        if value:
            raw.extend(_flatten_evidence_value(value))
    return _compact_texts(raw, limit=5)


def _flatten_evidence_value(value: Any) -> list[str]:
    """Flatten nested trace output into short display strings."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value[:5]:
            flattened.extend(_flatten_evidence_value(item))
        return flattened
    if isinstance(value, dict):
        name = value.get("name") or value.get("symbol") or value.get("title")
        symbol = value.get("symbol")
        score = value.get("score")
        rating = value.get("rating")
        trade_date_value = value.get("trade_date")
        parts = []
        if name:
            parts.append(str(name))
        if symbol and symbol != name:
            parts.append(str(symbol))
        if trade_date_value:
            parts.append(str(trade_date_value))
        if rating:
            parts.append(f"评级 {rating}")
        if isinstance(score, (int, float)):
            parts.append(f"评分 {score:.1f}")
        if parts:
            return [" · ".join(parts)]
    return []


def _compact_texts(values: list[Any], limit: int = 4) -> list[str]:
    """Deduplicate and trim text fragments for card facts."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:160])
        if len(result) >= limit:
            break
    return result


class AgentRunSummary(BaseModel):
    """Frontend-friendly summary of a persisted Agent execution."""

    run_id: str
    session_id: str
    status: Literal["success", "error"]
    intent: str | None = None
    message: str
    answer_preview: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list[AgentToolTrace] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class AgentRunsResponse(BaseModel):
    """Recent Agent execution summaries for observability UI."""

    runs: list[AgentRunSummary]
    generated_by: str


class AgentDataHealthTopCandidate(BaseModel):
    """Health status for one top-rated first-board candidate."""

    symbol: str
    name: str
    score: float
    rating: str
    feature_ready: bool
    enrichment_ready: bool = False
    similar_case_count: int
    similar_cases_with_post_bars: int


class AgentDataHealthResponse(BaseModel):
    """Health status for Agent data dependencies."""

    trade_date: date | None
    status: Literal["healthy", "partial", "missing"]
    raw_events_ready: bool
    raw_event_count: int
    first_board_features_ready: bool
    first_board_feature_count: int
    enrichment_ready: bool = False
    enrichment_count: int = 0
    top_candidates_checked: int
    similar_cases_ready: bool
    post_bars_ready: bool
    top_candidates: list[AgentDataHealthTopCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentSystemHealthResponse(BaseModel):
    """System status used by local startup checks and the frontend."""

    status: Literal["healthy", "partial", "missing"]
    current_date: date
    current_time: str
    latest_local_trade_date: date | None
    expected_data_date: date | None
    data_fresh: bool
    data_update_recommended: bool
    data_update_reason: str
    llm_enabled: bool
    llm_provider_configured: bool
    llm_model: str | None = None
    proxy_configured: bool
    proxy_warning: str | None = None
    offline_eval_passed: bool | None = None
    offline_eval_total: int | None = None
    offline_eval_failed: int | None = None
    data_health: AgentDataHealthResponse
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


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


