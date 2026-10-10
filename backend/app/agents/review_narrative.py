"""Evidence packaging and validation for optional review commentary."""

from collections import Counter
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.models import ReviewAgentPick, ReviewAgentReportResponse


ReviewSentence = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=8)
]


class ReviewNarrative(BaseModel):
    """A usable finding plus analysis; empty JSON is not a generated review."""

    main_findings: list[ReviewSentence] = Field(min_length=1, max_length=8)
    successful_patterns: list[ReviewSentence] = Field(default_factory=list, max_length=8)
    failed_patterns: list[ReviewSentence] = Field(default_factory=list, max_length=8)
    scoring_bias: list[ReviewSentence] = Field(default_factory=list, max_length=8)
    adjustment_suggestions: list[ReviewSentence] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def require_analysis(self):
        if not any((self.successful_patterns, self.failed_patterns,
                    self.scoring_bias, self.adjustment_suggestions)):
            raise ValueError("Review requires explanatory analysis or explicit limitations")
        return self


def authoritative_review_facts(
    report: ReviewAgentReportResponse,
    picks: list[ReviewAgentPick],
    feature_comparison: dict[str, Any],
    evaluation_scope: dict[str, Any],
) -> dict[str, Any]:
    """Always include full selected-cohort facts, independent of planner choices."""

    return {
        "deterministic_report": report.model_dump(mode="json", exclude={
            "reviewed_picks", "tool_results", "generated_by", "generation_mode",
            "llm_model", "generation_note", "confidence",
        }),
        "outcome_ready_count": sum(item.outcome_ready for item in picks),
        "evaluation_label_counts": dict(Counter(item.evaluation_label for item in picks)),
        "prediction_source_counts": dict(Counter(item.prediction_source for item in picks)),
        "forward_validation": {
            "eligible_sample_count": sum(item.time_cohort == "premarket_final" for item in picks),
            "cohort_counts": report.time_cohort_counts,
            "definition": "只有通过预测时间契约的premarket_final才具有现行盘前终选前向验证资格；close_baseline、legacy_close、historical_backtest等都不计入。不能用总样本减历史补算推断前向数量，prediction_source=live本身不证明前向资格。",
        },
        "incomplete_post_bar_count": sum(not item.post_bar_cache_complete for item in picks),
        "feature_comparison": feature_comparison,
        "comparison_basis": {
            "report_counts": "保留持久化预测的评分，仅使用复盘截至日内行情重算 evaluation_label：A/B评级次日开盘至收盘为正/负分别记success/miss，零收益为partial；低评级另记false_negative/avoid_success，不能并入失败或待观察。次日未到或必要行情/日历缺失时为pending。",
            "feature_groups": "按首板至复盘截至日内最新已缓存收盘的收益正负分组，并受真实交易日follow_days窗口约束；有洞行情保留真实日期位置并标不完整，不能把D+2当成D+1；零收益、不足两根K线或缺失首板基准价不入特征组。",
            "promotion": "仅统计交易日历中紧邻下一交易日且事件行情就绪的1进2对照；pick_outcomes的promotion_outcome_ready独立于OHLC的outcome_ready，未知晋级为null。特征组晋级指标还要求该样本OHLC就绪，未就绪不等于失败。",
            "metric_samples": "feature_comparison.metrics.sample_counts逐字段提供valid_count和missing_count；均值和晋级率只使用各自有效样本，不以组总数代替分母。",
            "inference": "分组和来源不同，不能混用分母；描述性差异不代表因果，也不能将历史补算当成前向预测。",
        },
        "sampling_limits": {
            **evaluation_scope,
            "tool_detail_limit": 20,
            "tool_details_truncated": len(picks) > 20,
            "response_pick_limit": 100,
            "response_picks_truncated": len(picks) > 100,
            "selected_cohort_size": len(picks),
            "note": "总体计数及特征使用完整入选集合；上游按该区间持久化预测总数设置候选上限，先按截至日重算再选每日TopN；工具明细仅前20条，返回明细最多100条，不可外推为全市场。",
        },
    }
