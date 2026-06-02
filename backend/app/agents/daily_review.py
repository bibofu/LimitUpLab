"""Rule-based daily market review agent."""

from app.agents.facts import build_daily_review_facts
from app.models import DailyReview, DailyReviewFacts, LimitUpEvent, MarketSummary


SENTIMENT_LABELS = {
    "heating": "升温",
    "diverging": "分歧",
    "cooling": "退潮",
}


def build_daily_review(
    summary: MarketSummary,
    events: list[LimitUpEvent],
) -> DailyReview:
    """Build a deterministic daily review from structured facts."""

    facts = build_daily_review_facts(summary=summary, events=events)
    return DailyReview(facts=facts, narrative=_render_daily_review(facts))


def _render_daily_review(facts: DailyReviewFacts) -> str:
    """Render review facts into concise Markdown without adding new claims."""

    sentiment_label = SENTIMENT_LABELS[facts.sentiment]
    ladder = "、".join(
        f"{item.board_height}板{item.count}只" for item in facts.board_ladder
    )
    industries = "、".join(facts.hot_industries) if facts.hot_industries else "暂无"
    risks = "；".join(facts.risk_signals) if facts.risk_signals else "未触发明显风险标签"

    return "\n".join(
        [
            f"### {facts.trade_date} 短线复盘",
            "",
            f"今日短线情绪判定为 **{sentiment_label}**。"
            f"涨停事件 {facts.limit_up_count} 个，首板 {facts.first_board_count} 个，"
            f"连板 {facts.continued_board_count} 个，最高高度 {facts.max_board_height} 板。",
            "",
            f"封板稳定性方面，盘中炸开或封板不稳样本 {facts.unstable_count} 个，"
            f"对应比例 {facts.failed_limit_up_rate:.0%}；"
            f"收盘未封住样本 {facts.unclosed_count} 个。",
            "",
            f"连板梯队：{ladder or '暂无'}。",
            f"热门行业：{industries}。",
            f"风险观察：{risks}。",
            "",
            "以上内容仅用于收盘后复盘和研究，不构成投资建议。",
        ]
    )
