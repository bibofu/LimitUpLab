"""Deterministic fact builders for agent-style review features."""

from collections import Counter

from app.models import BoardLadderItem, DailyReviewFacts, LimitUpEvent, MarketSummary
from app.services.analysis import events_for_date


def build_daily_review_facts(
    summary: MarketSummary,
    events: list[LimitUpEvent],
) -> DailyReviewFacts:
    """Build machine-verifiable facts for the latest daily market review."""

    latest_events = events_for_date(events, summary.trade_date)
    board_ladder_counts = Counter(event.board_height for event in latest_events)
    unclosed_count = sum(1 for event in latest_events if not event.closed_limit)
    risk_signals = _build_risk_signals(
        summary=summary,
        unclosed_count=unclosed_count,
    )

    return DailyReviewFacts(
        trade_date=summary.trade_date,
        sentiment=summary.sentiment,
        limit_up_count=summary.limit_up_count,
        first_board_count=summary.first_board_count,
        continued_board_count=summary.continued_board_count,
        unstable_count=summary.failed_count,
        unclosed_count=unclosed_count,
        failed_limit_up_rate=summary.failed_limit_up_rate,
        max_board_height=summary.max_board_height,
        total_amount=summary.total_amount,
        hot_industries=summary.hot_industries,
        board_ladder=[
            BoardLadderItem(board_height=height, count=count)
            for height, count in sorted(board_ladder_counts.items(), reverse=True)
        ],
        risk_signals=risk_signals,
    )


def _build_risk_signals(summary: MarketSummary, unclosed_count: int) -> list[str]:
    """Generate compact risk labels from deterministic market facts."""

    signals: list[str] = []

    if summary.failed_limit_up_rate >= 0.55:
        signals.append("封板稳定性偏弱")
    elif summary.failed_limit_up_rate >= 0.35:
        signals.append("封板分歧较高")

    if unclosed_count > 0:
        signals.append("存在收盘未封住样本")

    if summary.continued_board_count <= 2:
        signals.append("连板梯队偏薄")

    if summary.max_board_height >= 5:
        signals.append("高位连板仍有高度")

    return signals
