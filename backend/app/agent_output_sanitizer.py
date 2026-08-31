"""Keep internal Agent implementation names out of user-visible text."""

from __future__ import annotations

import re
from collections.abc import Callable


INTERNAL_TOOL_LABELS: dict[str, str] = {
    "market_summary": "市场概况",
    "market_index_trend": "大盘指数走势",
    "daily_board_promotion": "连板晋级统计",
    "sector_performance": "板块行情",
    "hot_stock_ranking": "热股排行",
    "dragon_tiger_list": "龙虎榜数据",
    "remote_limit_up_pool": "同花顺涨停池",
    "finance_news": "财经资讯",
    "stock_news": "个股资讯",
    "stock_activity": "个股近期动态",
    "web_search": "公开信息",
    "first_board_ratings": "首板评级",
    "first_board_filter": "首板筛选结果",
    "limit_up_events": "涨停事件数据",
    "stock_kline": "个股行情",
    "first_board_critic": "评分复核",
    "rating_backtest": "评分回测",
    "rating_evaluation": "预测评价",
    "review_high_score_picks": "高分票复盘",
    "prediction_quality_audit": "预测质量审计",
    "scoring_policy_status": "评分策略状态",
    "limit_up_event_dates": "本地交易日数据",
    "llm_tool_planner": "问题分析过程",
    "llm_tool_answer": "回答生成过程",
    "template_general_answer": "本地数据分析",
    "llm_planner_direct_answer": "直接回答",
    "agent_plan": "问题分析过程",
}

_INTERNAL_NAME_PATTERN = "|".join(
    re.escape(name)
    for name in sorted(INTERNAL_TOOL_LABELS, key=len, reverse=True)
)
_TOOL_REFERENCE_PATTERNS = (
    re.compile(
        rf"(?:数据|信息|结果|结论|回答)?\s*"
        rf"(?:来自|来源于|通过|使用(?:了)?|由)\s*"
        rf"`?(?:{_INTERNAL_NAME_PATTERN})`?\s*"
        rf"(?:工具|tool)(?:提供|返回|生成|查询)?",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"`?(?:{_INTERNAL_NAME_PATTERN})`?\s*"
        rf"(?:工具|tool)\s*(?:返回|提供|显示|查询到|生成)",
        flags=re.IGNORECASE,
    ),
)


def friendly_tool_label(tool_name: str) -> str:
    """Return a business-facing label without exposing an implementation key."""

    return INTERNAL_TOOL_LABELS.get(tool_name, "相关数据")


def sanitize_agent_answer(text: str) -> str:
    """Remove internal tool references while preserving the answer's facts."""

    sanitized = text
    for pattern in _TOOL_REFERENCE_PATTERNS:
        sanitized = pattern.sub("依据本地结构化数据", sanitized)
    for internal_name, label in INTERNAL_TOOL_LABELS.items():
        sanitized = re.sub(
            rf"`?{re.escape(internal_name)}`?",
            label,
            sanitized,
            flags=re.IGNORECASE,
        )
    sanitized = re.sub(
        r"(?:依据本地结构化数据)\s*(?:工具|tool)",
        "依据本地结构化数据",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


class AgentAnswerStreamSanitizer:
    """Sanitize complete clauses even when an LLM splits names across deltas."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.pending = ""

    def feed(self, delta: str) -> None:
        """Consume one raw model delta and emit only safe text."""

        self.pending += delta
        while match := re.search(r"[，,。！？；;\n]", self.pending):
            boundary = match.end()
            rendered = sanitize_agent_answer(self.pending[:boundary])
            self.pending = self.pending[boundary:]
            if rendered:
                self.emit(rendered)

    def flush(self) -> None:
        """Emit the remaining safe tail after model streaming completes."""

        rendered = sanitize_agent_answer(self.pending)
        self.pending = ""
        if rendered:
            self.emit(rendered)
