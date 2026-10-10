"""Presentation of current and legacy Agent tool evidence for saved chat responses."""

from typing import Any


def _evidence_title_kind(tool_name: str) -> tuple[str, str]:
    """Map internal tool names to UI evidence categories."""

    mapping: dict[str, tuple[str, str]] = {
        "agent_plan": ("问题理解与工具计划", "execution"),
        "llm_tool_planner": ("LLM 工具规划", "execution"),
        "market_summary": ("市场环境事实", "market"),
        "sector_performance": ("行业板块行情", "market"),
        "sector_stock_ranking": ("板块成分股走势", "market"),
        "hot_stock_ranking": ("同花顺热股榜", "market"),
        "dragon_tiger_list": ("同花顺龙虎榜", "market"),
        "remote_limit_up_pool": ("同花顺涨停池", "limit_up_events"),
        "finance_news": ("财经快讯聚合", "tool"),
        "stock_news": ("个股资讯", "tool"),
        "stock_activity": ("个股近期动态", "tool"),
        "web_search": ("公开网络检索", "tool"),
        "first_board_ratings": ("首板候选池与评分", "candidate_pool"),
        "market_event_pool": ("市场事件查询", "limit_up_events"),
        "limit_up_events": ("涨停事件查询", "limit_up_events"),
        "post_limit_screen": ("涨停后形态筛选", "candidate_pool"),
        "post_limit_path": ("涨停后逐日走势", "tool"),
        "post_limit_statistics": ("涨停后历史统计", "evaluation"),
        "first_board_filter": ("首板条件筛选", "candidate_pool"),
        "first_board_critic": ("评分反证与风险", "critic"),
        "rating_backtest": ("评分历史回测", "evaluation"),
        "rating_evaluation": ("Agent 自我评价", "evaluation"),
        "scoring_policy_status": ("评分策略迭代", "evaluation"),
        "limit_up_event_dates": ("本地数据日期", "data_availability"),
    }
    return mapping.get(tool_name, (tool_name, "tool"))


def _evidence_metrics(output: dict[str, Any]) -> dict[str, Any]:
    """Extract displayable numeric/string metrics from trace output."""

    allowed = (
        "trade_date",
        "data_as_of",
        "captured_at",
        "fetched_at",
        "window_hours",
        "period",
        "source",
        "sector_name",
        "sector_count",
        "member_count",
        "analyzed_count",
        "missing_count",
        "truncated_count",
        "requested_days",
        "requested_limit",
        "rank",
        "change_pct",
        "up_count",
        "down_count",
        "candidate_count",
        "matched_count",
        "pool_count",
        "evaluable_count",
        "coverage_ratio",
        "complete_sample_count",
        "complete_signal_date_count",
        "sample_quality",
        "rule_version",
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
