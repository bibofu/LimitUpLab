"""Daily market review playbook; data stays in tools, this file only orchestrates."""

from __future__ import annotations

from typing import Any


REVIEW_SKILL_TERMS = (
    "复盘", "收盘总结", "今日总结", "今天市场", "今日市场",
    "市场怎么样", "市场情绪", "盘后总结", "收盘复盘",
)

DAILY_REVIEW_SKILL = """【每日复盘工作流】
适用：用户要求复盘、收盘总结、今天市场怎么样、市场情绪、首板/连板结构、板块轮动、龙虎榜或指数复盘。
原则：先取当日全市场聚合，再按用户关注点展开；不要给每个用户套同一份固定指标清单。

0. 日期与范围
- 用户明确日期时使用该日期；否则用最近已收盘交易日。需要确认时调用 trading_calendar。
- 非交易日回退到最近已收盘交易日并说明；所有数字注明日期和市场范围。

1. 基础事实（每次复盘都取一次）
- 调用 market_event_pool(event_type="limit_up", result_mode="summary", trade_date=D, market=用户指定市场或null)。
- 直接引用 summary 中的 touched_count、closed_count、opened_count、unsealed_count、opened_rate、unsealed_rate、first_board_count、continued_board_count、max_board_height、top_limit_up_industries。
- 开板率和未回封率只能引用工具计算的分子分母，禁止用事件行相加重算；缺失不得写成 0。

2. 用户未指定重点时的紧凑默认复盘
- 市场情绪：基础事实的比率、最高板。
- 首板/连板结构：limit_up_events(trade_date=D, event_status="closed", group_by="industry", limit=5)，必要时 board_height=1 或 min_board_height=2。
- 晋级观察：daily_board_promotion(days=5, end_date=D)，引用工具给出的分子分母。
- 输出顺序：日期与数据范围 → 3-5 条核心事实 → 首板/连板结构 → 晋级 → 数据缺口；结尾提示可继续看板块轮动、龙虎榜、指数或评分。

3. 用户指定重点时只展开对应模块
- 板块轮动：sector_performance(trade_date=D, category="industry")；需要概念时 category="cn_concept"。板块涨跌幅与 top_limit_up_industries（收盘涨停计数）分开说明口径。
- 龙虎榜/资金：dragon_tiger_list(trade_date=D)；机构净买与游资净买不能直接相加。
- 指数/大盘：market_index_trend(days=5, end_date=D)；不得由 Top/Bottom 推断全市场普涨普跌。
- 评分/Top10/候选：first_board_ratings 或 review_high_score_picks；只陈述评分与后续表现，不输出买卖建议。
- 跌停/风险：market_event_pool(event_type="limit_down", result_mode="count", trade_date=D)。
- 首板/连板名单：limit_up_events(...)；用户要求完整名单时覆盖全部返回行并核对 row_count。

4. 输出边界
- 每个数字注明日期、口径、来源；ranking_complete=false、data_fresh=false、truncated、data_missing 必须说明。
- 不输出买入、卖出、仓位、目标价、收益承诺或确定性预测；不用历史回答替代本轮工具结果；不把缺失当零。
"""


def resolve_agent_skill(request: Any | None) -> str | None:
    """Select a stable playbook from the user's own wording."""

    message = str(getattr(request, "message", "") or "")
    if any(term in message for term in REVIEW_SKILL_TERMS):
        return DAILY_REVIEW_SKILL
    return None
