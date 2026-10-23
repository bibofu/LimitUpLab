"""Documented output contracts for post-limit and review tools."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from app.agents.tool_output_models import OutputModel


class AgentEvaluationRow(OutputModel):
    prediction_id: str = Field(description="预测记录ID")
    trade_date: date = Field(description="预测交易日")
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    score: float = Field(description="首板评分")
    rating: str = Field(description="首板评级")
    confidence: float = Field(description="评级置信度")
    prediction_source: str = Field(description="预测来源：live或historical_backtest")
    data_as_of: date = Field(description="预测数据截止日")
    time_cohort: str = Field(description="时间分组")
    scoring_version: str | None = Field(default=None, description="评分版本")
    evaluation_label: str = Field(
        description="评价标签：success、partial、miss、avoid_success、false_negative或pending"
    )
    outcome_ready: bool = Field(description="后续结果是否可用")
    promoted_to_second_board: bool = Field(description="是否晋级二板")
    next_high_pct: float | None = Field(default=None, description="次日最高价涨跌幅", json_schema_extra={"unit": "%"})
    next_close_pct: float | None = Field(default=None, description="次日收盘涨跌幅", json_schema_extra={"unit": "%"})
    next_open_to_high_pct: float | None = Field(default=None, description="次日开盘到最高涨跌幅", json_schema_extra={"unit": "%"})
    next_open_to_low_pct: float | None = Field(default=None, description="次日开盘到最低涨跌幅", json_schema_extra={"unit": "%"})
    next_open_to_close_pct: float | None = Field(default=None, description="次日开盘到收盘涨跌幅", json_schema_extra={"unit": "%"})
    three_day_high_pct: float | None = Field(default=None, description="3日最高价涨跌幅", json_schema_extra={"unit": "%"})
    three_day_close_pct: float | None = Field(default=None, description="3日收盘涨跌幅", json_schema_extra={"unit": "%"})
    three_day_open_to_close_pct: float | None = Field(default=None, description="3日开盘到收盘涨跌幅", json_schema_extra={"unit": "%"})
    max_drawdown_from_next_open_3d: float | None = Field(default=None, description="次日开盘后3日最大回撤", json_schema_extra={"unit": "%"})
    lesson: str = Field(description="该样本的经验总结")
    scoring_suggestion: str = Field(description="评分改进建议")


class AgentEvaluationPayload(OutputModel):
    start_date: date = Field(description="评价起始日")
    end_date: date = Field(description="评价截止日")
    prediction_count: int = Field(description="预测记录数")
    outcome_ready_count: int = Field(description="结果可用记录数")
    source_counts: dict[str, int] = Field(description="按预测来源计数")
    time_cohort_counts: dict[str, int] = Field(description="按时间分组计数")
    excluded_time_prediction_count: int = Field(description="因时间不可验证被排除的记录数")
    label_counts: dict[str, int] = Field(description="按评价标签计数")
    evaluations: list[AgentEvaluationRow] = Field(description="逐条评价")
    summary: list[str] = Field(description="评价摘要")
    warnings: list[str] = Field(description="边界警告")
    generated_by: str = Field(description="生成来源")


class FirstBoardCriticPayload(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trade_date: date = Field(description="首板交易日")
    rating: str = Field(description="原评级")
    score: float = Field(description="原评分")
    original_confidence: float = Field(description="原置信度")
    suggested_confidence: float = Field(description="建议置信度")
    confidence_delta: float = Field(description="建议置信度变化")
    verdict: str = Field(description="复核结论：supportive、cautious或fragile")
    support_evidence: list[str] = Field(description="支持证据")
    counter_evidence: list[str] = Field(description="反证")
    missing_data: list[str] = Field(description="缺失数据")
    critic_warnings: list[str] = Field(description="复核警告")
    review_questions: list[str] = Field(description="需要继续核实的问题")
    generated_by: str = Field(description="生成来源")


class RatingBacktestPayload(OutputModel):
    start_date: date = Field(description="回测起始日")
    end_date: date = Field(description="回测截止日")
    trade_dates: list[date] = Field(description="回测交易日")
    sample_size: int = Field(description="样本数")
    outcome_ready_count: int = Field(description="后续结果可用样本数")
    buckets: list[dict[str, Any]] = Field(
        description="按A/B/C/D分桶的表现，含样本数、次日和3日收益、晋级率"
    )
    failure_samples: list[dict[str, Any]] = Field(
        description="高评分但后续表现较弱的样本，含symbol、name、trade_date、rating、score、收益和原因"
    )
    observations: list[str] = Field(description="评分自我评价")
    warnings: list[str] = Field(description="边界警告")
    generated_by: str = Field(description="生成来源")


class PredictionQualityAuditPayload(OutputModel):
    start_date: date = Field(description="审计起始日")
    end_date: date = Field(description="审计截止日")
    latest_trade_date: date = Field(description="本地最新交易日")
    audited_scoring_version: str = Field(description="被审计评分版本")
    top_k: int = Field(description="Top K口径")
    raw_prediction_rows: int = Field(description="原始预测行数")
    audited_prediction_rows: int = Field(description="纳入审计的预测行数")
    canonical_prediction_count: int = Field(description="去重后的预测数")
    cross_cohort_duplicate_rows: int = Field(description="跨来源/版本重复行数")
    data_as_of_violation_count: int = Field(description="数据时点违规行数")
    prediction_trade_date_count: int = Field(description="预测交易日数")
    next_day_mature_trade_date_count: int = Field(description="次日结果成熟交易日数")
    complete_next_day_trade_date_count: int = Field(description="次日结果完整交易日数")
    next_day_outcome_coverage_rate: float = Field(description="次日结果覆盖率", json_schema_extra={"unit": "%"})
    three_day_outcome_coverage_rate: float = Field(description="3日结果覆盖率", json_schema_extra={"unit": "%"})
    cohorts: list[dict[str, Any]] = Field(description="按来源或评分版本分组的覆盖")
    date_coverage: list[dict[str, Any]] = Field(description="逐预测日的结果成熟度")
    benchmarks: list[dict[str, Any]] = Field(description="确定性基线表现")
    benchmark_cohorts: dict[str, list[dict[str, Any]]] = Field(description="按分组拆分的基线")
    time_cohort_counts: dict[str, int] = Field(description="时间分组计数")
    excluded_time_prediction_count: int = Field(description="因时间不可验证被排除的预测数")
    strict_forward_prediction_count: int = Field(description="严格前向预测数")
    policy_status: dict[str, Any] = Field(description="Champion/Challenger晋级状态")
    findings: list[str] = Field(description="审计发现")
    recommendations: list[str] = Field(description="改进建议")
    warnings: list[str] = Field(description="边界警告")
    generated_by: str = Field(description="生成来源")


class ReviewHighScorePicksPayload(OutputModel):
    generation_mode: str = Field(description="生成模式：llm、deterministic或legacy")
    llm_model: str | None = Field(default=None, description="生成使用的模型")
    generation_note: str | None = Field(default=None, description="生成方式说明")
    start_date: date = Field(description="复盘起始日")
    end_date: date = Field(description="复盘截止日")
    sample_size: int = Field(description="复盘样本数")
    time_cohort_counts: dict[str, int] = Field(description="时间分组计数")
    time_audit_status: str = Field(description="时间审计状态")
    excluded_time_prediction_count: int = Field(description="因时间不可验证被排除的预测数")
    success_count: int = Field(description="成功样本数")
    failed_count: int = Field(description="失败样本数")
    pending_count: int = Field(description="结果待定样本数")
    promotion_ready_date_count: int = Field(description="可比较晋级率的交易日数")
    top_pick_promotion_sample_size: int = Field(description="Top票晋级率分母")
    top_pick_promoted_count: int = Field(description="Top票晋级数")
    top_pick_promotion_rate: float | None = Field(default=None, description="Top票晋级率", json_schema_extra={"unit": "%"})
    market_promotion_sample_size: int = Field(description="全市场首板晋级率分母")
    market_promoted_count: int = Field(description="全市场首板晋级数")
    market_promotion_rate: float | None = Field(default=None, description="全市场首板晋级率", json_schema_extra={"unit": "%"})
    promotion_rate_delta: float | None = Field(default=None, description="Top票相对全市场晋级率差", json_schema_extra={"unit": "%"})
    promotion_comparisons: list[dict[str, Any]] = Field(description="逐日晋级率对照")
    main_findings: list[str] = Field(description="主要发现")
    successful_patterns: list[str] = Field(description="成功模式")
    failed_patterns: list[str] = Field(description="失败模式")
    scoring_bias: list[str] = Field(description="评分偏差")
    adjustment_suggestions: list[str] = Field(description="评分调整建议")
    confidence: float = Field(description="复盘结论置信度")
    reviewed_picks: list[dict[str, Any]] = Field(description="逐票复盘明细")
    warnings: list[str] = Field(description="边界警告")
    generated_by: str = Field(description="生成来源")


class ScoringPolicyStatusPayload(OutputModel):
    champion: dict[str, Any] = Field(
        description="当前Champion，含version、status、factor_weights、source、rationale、训练区间和时间戳"
    )
    policy_count: int = Field(description="已注册策略数")
    challengers: list[dict[str, Any]] = Field(description="近期Challenger策略")
    latest_optimization: dict[str, Any] | None = Field(
        default=None, description="最近一次样本外优化结果，含比较指标和晋级门槛"
    )
