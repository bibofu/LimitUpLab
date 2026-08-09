"""Rule-based first-board rating agent.

The module keeps the first MVP deterministic: data is filtered and scored from
structured facts before any future LLM explanation layer is added.
"""

from collections import Counter
from datetime import date, time
from typing import Optional

from app.models import (
    FirstBoardCandidateFacts,
    FirstBoardFilterResult,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    ScoreBreakdownItem,
)
from app.services.analysis import events_for_date, latest_trade_date, summarize_market


MIN_AMOUNT = 100_000_000
FIRST_BOARD_AGENT_VERSION = "first-board-rule-v1"


def build_first_board_ratings(
    events: list[LimitUpEvent],
    trade_date: Optional[date] = None,
) -> FirstBoardRatingsResponse:
    """Build first-board ratings for a trade date from persisted events."""

    target_date = trade_date or latest_trade_date(events)
    latest_events = events_for_date(events, target_date)
    summary = summarize_market(latest_events)
    filter_results = [_evaluate_candidate_filter(event) for event in latest_events]
    included_symbols = {
        result.symbol for result in filter_results if result.included
    }
    facts = [
        build_first_board_candidate_facts(
            event=event,
            same_day_events=latest_events,
            summary=summary,
            data_missing=next(
                result.data_missing
                for result in filter_results
                if result.symbol == event.symbol
            ),
        )
        for event in latest_events
        if event.symbol in included_symbols
    ]

    return FirstBoardRatingsResponse(
        trade_date=target_date,
        candidates=sorted(
            [_rate_candidate(item) for item in facts],
            key=lambda item: (-item.score, -item.confidence, item.facts.first_limit_time),
        ),
        filtered_out=[result for result in filter_results if not result.included],
        universe_count=len(latest_events),
        generated_by=FIRST_BOARD_AGENT_VERSION,
    )


def build_first_board_candidate_facts(
    event: LimitUpEvent,
    same_day_events: list[LimitUpEvent],
    summary: MarketSummary,
    data_missing: Optional[list[str]] = None,
) -> FirstBoardCandidateFacts:
    """Create verifiable first-board facts for one included candidate."""

    closed_limit_events = [item for item in same_day_events if item.closed_limit]
    industry_counts = Counter(item.industry for item in closed_limit_events)
    concept_counts = Counter(item.concept for item in closed_limit_events)

    return FirstBoardCandidateFacts(
        symbol=event.symbol,
        name=event.name,
        trade_date=event.trade_date,
        first_limit_time=event.first_limit_time,
        last_limit_time=event.last_limit_time,
        seal_count=event.seal_count,
        break_count=event.break_count,
        closed_limit=event.closed_limit,
        board_height=event.board_height,
        amount=event.amount,
        turnover_rate=event.turnover_rate,
        industry=event.industry,
        concept=event.concept,
        same_industry_limit_up_count=industry_counts[event.industry],
        same_concept_limit_up_count=concept_counts[event.concept],
        market_limit_up_count=summary.limit_up_count,
        market_first_board_count=summary.first_board_count,
        market_failed_limit_up_rate=summary.failed_limit_up_rate,
        market_max_board_height=summary.max_board_height,
        market_sentiment=summary.sentiment,
        data_missing=data_missing or [],
    )


def _evaluate_candidate_filter(event: LimitUpEvent) -> FirstBoardFilterResult:
    """Evaluate hard candidate-pool filters and record unavailable fields."""

    excluded_reasons: list[str] = []
    data_missing: list[str] = []

    if event.board_height != 1:
        excluded_reasons.append("非首板")
    if not event.closed_limit:
        excluded_reasons.append("收盘未封住")
    if _is_risk_warning_name(event.name):
        excluded_reasons.append("ST 或退市风险警示")
    if _is_beijing_stock_exchange(event.symbol):
        excluded_reasons.append("北交所股票")
    if _is_star_market(event.symbol):
        excluded_reasons.append("科创板股票")
    if _looks_like_new_stock_name(event.name):
        excluded_reasons.append("新股 / 次新股")
    else:
        data_missing.append("listing_date")
    if event.amount < MIN_AMOUNT:
        excluded_reasons.append("成交额过小")

    return FirstBoardFilterResult(
        symbol=event.symbol,
        name=event.name,
        included=len(excluded_reasons) == 0,
        excluded_reasons=excluded_reasons,
        data_missing=data_missing,
    )


def _rate_candidate(facts: FirstBoardCandidateFacts) -> FirstBoardRating:
    """Score one first-board candidate with transparent factor weights."""

    breakdown = [
        _score_first_limit_time(facts.first_limit_time),
        _score_seal_stability(facts.break_count, facts.closed_limit),
        _score_seal_pressure(facts.seal_count),
        _score_turnover(facts.turnover_rate),
        _score_amount(facts.amount),
        _score_industry_heat(facts.same_industry_limit_up_count),
        _score_market_context(facts.market_failed_limit_up_rate, facts.market_max_board_height),
    ]
    raw_score = sum(item.score for item in breakdown)
    score = round(max(0, min(100, raw_score)), 1)
    confidence = _calculate_confidence(facts)

    return FirstBoardRating(
        facts=facts,
        score=score,
        rating=_rating_for_score(score),
        confidence=confidence,
        score_breakdown=breakdown,
        reasons=_build_reasons(facts),
        risks=_build_risks(facts),
    )


def _score_first_limit_time(value: time) -> ScoreBreakdownItem:
    """Score earlier first seals higher than late-day seals."""

    minutes = value.hour * 60 + value.minute
    if minutes <= 9 * 60 + 45:
        score, label = 25, "首次封板处于早盘强势区间"
    elif minutes <= 10 * 60 + 30:
        score, label = 20, "首次封板处于早盘偏强区间"
    elif minutes <= 11 * 60 + 30:
        score, label = 15, "首次封板处于上午后段"
    elif minutes <= 13 * 60 + 30:
        score, label = 10, "首次封板偏午后"
    else:
        score, label = 6, "首次封板时间偏晚"

    return ScoreBreakdownItem(
        name="首封时间",
        score=score,
        max_score=25,
        evidence=[f"首次封板时间 {value.strftime('%H:%M')}，{label}"],
    )


def _score_seal_stability(break_count: int, closed_limit: bool) -> ScoreBreakdownItem:
    """Score intraday seal stability from break count and close status."""

    if not closed_limit:
        score, label = 0, "收盘未封住"
    elif break_count == 0:
        score, label = 25, "炸板次数 0 次，封板稳定性较好"
    elif break_count == 1:
        score, label = 18, "炸板 1 次，存在一定分歧"
    elif break_count <= 3:
        score, label = 10, f"炸板 {break_count} 次，分歧较高"
    else:
        score, label = 5, f"炸板 {break_count} 次，封板压力较大"

    return ScoreBreakdownItem(
        name="封板稳定性",
        score=score,
        max_score=25,
        evidence=[label],
    )


def _score_seal_pressure(seal_count: int) -> ScoreBreakdownItem:
    """Score repeated sealing attempts as a pressure signal."""

    if seal_count <= 1:
        score, label = 12, "封板次数少，盘中反复压力较低"
    elif seal_count == 2:
        score, label = 10, "封板 2 次，分歧可控"
    elif seal_count <= 4:
        score, label = 6, f"封板 {seal_count} 次，盘中反复较明显"
    else:
        score, label = 3, f"封板 {seal_count} 次，回封压力较大"

    return ScoreBreakdownItem(
        name="封板次数",
        score=score,
        max_score=12,
        evidence=[label],
    )


def _score_turnover(turnover_rate: float) -> ScoreBreakdownItem:
    """Score turnover rate as liquidity and disagreement balance."""

    if 3 <= turnover_rate <= 12:
        score, label = 12, "换手率处于相对适中区间"
    elif 1 <= turnover_rate < 3 or 12 < turnover_rate <= 18:
        score, label = 8, "换手率略偏离适中区间"
    else:
        score, label = 4, "换手率过低或过高，样本可比性下降"

    return ScoreBreakdownItem(
        name="换手率",
        score=score,
        max_score=12,
        evidence=[f"换手率 {turnover_rate:.1f}%，{label}"],
    )


def _score_amount(amount: float) -> ScoreBreakdownItem:
    """Score trading amount to avoid tiny or over-consumed samples."""

    if 300_000_000 <= amount <= 3_000_000_000:
        score, label = 10, "成交额处于可观察区间"
    elif 100_000_000 <= amount < 300_000_000 or 3_000_000_000 < amount <= 8_000_000_000:
        score, label = 7, "成交额略偏离适中区间"
    else:
        score, label = 4, "成交额过小或过大，流动性/资金消耗需注意"

    return ScoreBreakdownItem(
        name="成交额",
        score=score,
        max_score=10,
        evidence=[f"成交额 {amount / 100_000_000:.1f} 亿，{label}"],
    )


def _score_industry_heat(count: int) -> ScoreBreakdownItem:
    """Score same-industry limit-up concentration as topic diffusion."""

    if count >= 5:
        score, label = 8, "同行业涨停较多，板块扩散较强"
    elif count >= 3:
        score, label = 6, "同行业有一定涨停扩散"
    elif count >= 2:
        score, label = 4, "同行业存在少量联动"
    else:
        score, label = 2, "同行业涨停较少，板块扩散不足"

    return ScoreBreakdownItem(
        name="行业热度",
        score=score,
        max_score=8,
        evidence=[f"同行业涨停 {count} 只，{label}"],
    )


def _score_market_context(failed_rate: float, max_board_height: int) -> ScoreBreakdownItem:
    """Score the broader short-term market context."""

    score = 8
    evidence: list[str] = []

    if failed_rate >= 0.55:
        score -= 5
        evidence.append(f"市场炸板率 {failed_rate:.0%}，接力环境偏弱")
    elif failed_rate >= 0.35:
        score -= 2
        evidence.append(f"市场炸板率 {failed_rate:.0%}，分歧偏高")
    else:
        evidence.append(f"市场炸板率 {failed_rate:.0%}，封板环境较稳定")

    if max_board_height >= 4:
        score += 2
        evidence.append(f"最高连板 {max_board_height} 板，短线高度仍在")
    elif max_board_height <= 2:
        score -= 2
        evidence.append(f"最高连板 {max_board_height} 板，连板高度偏低")

    return ScoreBreakdownItem(
        name="市场环境",
        score=max(0, min(8, score)),
        max_score=8,
        evidence=evidence,
    )


def _calculate_confidence(facts: FirstBoardCandidateFacts) -> float:
    """Calculate rating reliability separately from candidate strength."""

    confidence = 0.9
    confidence -= 0.08 * len(facts.data_missing)

    if facts.market_failed_limit_up_rate >= 0.55:
        confidence -= 0.12
    elif facts.market_failed_limit_up_rate >= 0.35:
        confidence -= 0.06

    if facts.amount < MIN_AMOUNT * 3:
        confidence -= 0.08
    if facts.turnover_rate > 18 or facts.turnover_rate < 1:
        confidence -= 0.08

    return round(max(0.35, min(0.95, confidence)), 2)


def _rating_for_score(score: float) -> str:
    """Map numeric score to the A/B/C/D rating bands."""

    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _build_reasons(facts: FirstBoardCandidateFacts) -> list[str]:
    """Build concise positive observations from candidate facts."""

    reasons: list[str] = []
    if facts.first_limit_time <= time(9, 45):
        reasons.append("首封时间较早")
    if facts.break_count == 0:
        reasons.append("炸板次数为 0")
    if 3 <= facts.turnover_rate <= 12:
        reasons.append("换手率处于相对适中区间")
    if facts.same_industry_limit_up_count >= 3:
        reasons.append("同行业涨停扩散较好")
    if facts.market_max_board_height >= 4:
        reasons.append("市场仍有连板高度")

    return reasons or ["首板基础条件满足，但优势信号不突出"]


def _build_risks(facts: FirstBoardCandidateFacts) -> list[str]:
    """Build risk labels without turning them into trading advice."""

    risks: list[str] = []
    if facts.data_missing:
        risks.append(f"缺失字段：{', '.join(facts.data_missing)}")
    if facts.break_count > 0:
        risks.append(f"盘中炸板 {facts.break_count} 次")
    if facts.seal_count >= 4:
        risks.append("封板次数较多，盘中分歧较大")
    if facts.turnover_rate > 18:
        risks.append("换手率偏高")
    elif facts.turnover_rate < 1:
        risks.append("换手率偏低")
    if facts.market_failed_limit_up_rate >= 0.55:
        risks.append("当日市场炸板率偏高")
    if facts.same_industry_limit_up_count <= 1:
        risks.append("同行业扩散不足")

    return risks or ["未触发明显风险标签"]


def _is_risk_warning_name(name: str) -> bool:
    """Return true for ST, *ST, or delisting-risk names."""

    normalized = name.upper()
    return "ST" in normalized or "退" in name


def _is_beijing_stock_exchange(symbol: str) -> bool:
    """Infer Beijing Stock Exchange symbols from common A-share prefixes."""

    return symbol.startswith(("4", "8", "920"))


def _is_star_market(symbol: str) -> bool:
    """Infer STAR Market symbols from common Shanghai prefixes."""

    return symbol.startswith(("688", "689"))


def _looks_like_new_stock_name(name: str) -> bool:
    """Detect first-days new-stock markers when listing date is unavailable."""

    return name.startswith(("N", "C"))
