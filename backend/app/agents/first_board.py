"""Rule-based first-board rating agent.

The module keeps the first MVP deterministic: data is filtered and scored from
structured facts before any future LLM explanation layer is added.
"""

from collections import Counter
from datetime import date, time
from typing import Optional

from app.models import (
    FirstBoardCandidateFacts,
    FirstBoardEnrichmentSnapshot,
    FirstBoardFilterResult,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    ScoreBreakdownItem,
    ScoringPolicy,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.analysis import events_for_date, latest_trade_date, summarize_market
from app.services.scoring_policy import (
    DEFAULT_SCORING_POLICY_VERSION,
    FACTOR_KEYS_BY_NAME,
    build_default_scoring_policy,
    validate_policy_factor_keys,
)


MIN_AMOUNT = 100_000_000
FIRST_BOARD_AGENT_VERSION = DEFAULT_SCORING_POLICY_VERSION


def build_first_board_ratings(
    events: list[LimitUpEvent],
    trade_date: Optional[date] = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    scoring_policy: ScoringPolicy | None = None,
) -> FirstBoardRatingsResponse:
    """Build first-board ratings for a trade date from persisted events."""

    target_date = trade_date or latest_trade_date(events)
    latest_events = events_for_date(events, target_date)
    summary = summarize_market(latest_events)
    repository = first_board_repository or SQLiteFirstBoardRepository()
    active_policy = scoring_policy or (
        SQLiteScoringPolicyRepository(repository.database_path).get_champion()
        or build_default_scoring_policy()
    )
    validate_policy_factor_keys(active_policy)
    enrichments = {
        item.symbol: item
        for item in repository.list_enrichment_for_date(target_date)
    }
    filter_results = [
        _evaluate_candidate_filter(event, enrichments.get(event.symbol))
        for event in latest_events
    ]
    included_symbols = {
        result.symbol for result in filter_results if result.included
    }
    facts = [
        build_first_board_candidate_facts(
            event=event,
            same_day_events=latest_events,
            summary=summary,
            enrichment=enrichments.get(event.symbol),
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
            [_rate_candidate(item, active_policy) for item in facts],
            key=lambda item: (-item.score, -item.confidence, item.facts.symbol),
        ),
        filtered_out=[result for result in filter_results if not result.included],
        universe_count=len(latest_events),
        generated_by=active_policy.version,
    )


def build_first_board_candidate_facts(
    event: LimitUpEvent,
    same_day_events: list[LimitUpEvent],
    summary: MarketSummary,
    enrichment: FirstBoardEnrichmentSnapshot | None = None,
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
        is_one_word_board=_is_one_word_board(event),
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
        enrichment=enrichment,
        data_missing=data_missing or [],
    )


def _evaluate_candidate_filter(
    event: LimitUpEvent,
    enrichment: FirstBoardEnrichmentSnapshot | None = None,
) -> FirstBoardFilterResult:
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
    if enrichment and enrichment.listing_age_days is not None and enrichment.listing_age_days < 120:
        excluded_reasons.append("上市未满 120 天")
    elif _looks_like_new_stock_name(event.name):
        excluded_reasons.append("新股 / 次新股")
    elif enrichment:
        data_missing.extend(enrichment.data_missing)
    else:
        data_missing.extend(["enrichment_snapshot", "listing_date"])
    if event.amount < MIN_AMOUNT:
        excluded_reasons.append("成交额过小")

    return FirstBoardFilterResult(
        symbol=event.symbol,
        name=event.name,
        included=len(excluded_reasons) == 0,
        excluded_reasons=excluded_reasons,
        data_missing=data_missing,
    )


def _rate_candidate(
    facts: FirstBoardCandidateFacts,
    scoring_policy: ScoringPolicy,
) -> FirstBoardRating:
    """Score one first-board candidate with transparent factor weights."""

    base_breakdown = [
        _score_first_limit_time(
            facts.first_limit_time,
            is_one_word_board=facts.is_one_word_board,
        ),
        _score_seal_stability(facts.break_count, facts.closed_limit),
        _score_seal_pressure(facts.seal_count),
        _score_turnover(facts.turnover_rate),
        _score_amount(facts.amount),
        _score_industry_heat(facts.same_industry_limit_up_count),
        _score_market_context(facts.market_failed_limit_up_rate, facts.market_max_board_height),
    ]
    enrichment_breakdown = [
        _score_pre_limit_structure(facts.enrichment),
        _score_sector_and_relay(facts.enrichment),
        _score_profile_and_history(facts.enrichment),
        _score_dragon_tiger(facts.enrichment),
        _score_popularity(facts.enrichment),
    ]
    breakdown = _apply_scoring_policy(
        [*base_breakdown, *enrichment_breakdown],
        scoring_policy,
    )
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


def _apply_scoring_policy(
    items: list[ScoreBreakdownItem],
    scoring_policy: ScoringPolicy,
) -> list[ScoreBreakdownItem]:
    """Scale raw factor scores to the active policy's 100-point weights."""

    weighted: list[ScoreBreakdownItem] = []
    for item in items:
        factor_key = FACTOR_KEYS_BY_NAME.get(item.name)
        if factor_key is None:
            raise ValueError(f"No scoring policy key registered for factor: {item.name}")
        target_weight = scoring_policy.factor_weights[factor_key]
        ratio = target_weight / item.max_score if item.max_score else 0.0
        weighted.append(_scale_score_item(item, ratio))
    return weighted


def _scale_score_item(item: ScoreBreakdownItem, ratio: float) -> ScoreBreakdownItem:
    """Scale legacy factor weights while retaining their evidence."""

    return ScoreBreakdownItem(
        name=item.name,
        score=round(item.score * ratio, 2),
        max_score=round(item.max_score * ratio, 2),
        evidence=item.evidence,
    )


def _score_pre_limit_structure(
    enrichment: FirstBoardEnrichmentSnapshot | None,
) -> ScoreBreakdownItem:
    """Score 60-day price, volume and moving-average structure."""

    if enrichment is None or enrichment.kline_bar_count < 20:
        return ScoreBreakdownItem(
            name="涨停前走势结构",
            score=7.5,
            max_score=15,
            evidence=["历史 K 线不足，按中性分处理并降低置信度"],
        )

    score = 0.0
    evidence: list[str] = []
    return_20d = enrichment.return_20d_pct
    if return_20d is not None:
        if -5 <= return_20d <= 25:
            score += 4
            evidence.append(f"近 20 日涨幅 {return_20d:.1f}%，未出现明显过热")
        elif 25 < return_20d <= 40:
            score += 2.5
            evidence.append(f"近 20 日涨幅 {return_20d:.1f}%，已有一定涨幅")
        else:
            score += 1
            evidence.append(f"近 20 日涨幅 {return_20d:.1f}%，趋势偏弱或偏热")

    distance = enrichment.distance_60d_high_pct
    if distance is not None:
        if distance >= -5:
            score += 4
            evidence.append(f"距 60 日高点 {distance:.1f}%，接近平台突破")
        elif distance >= -20:
            score += 3
            evidence.append(f"距 60 日高点 {distance:.1f}%，上方空间适中")
        else:
            score += 1
            evidence.append(f"距 60 日高点 {distance:.1f}%，仍处于深度回撤区")

    volume_ratio = enrichment.volume_ratio_5d
    if volume_ratio is not None:
        if 1.5 <= volume_ratio <= 4:
            score += 3
            evidence.append(f"量能为前 5 日均量的 {volume_ratio:.1f} 倍，放量较健康")
        elif 1 <= volume_ratio < 1.5 or 4 < volume_ratio <= 6:
            score += 2
            evidence.append(f"量能为前 5 日均量的 {volume_ratio:.1f} 倍")
        else:
            score += 1
            evidence.append(f"量能比 {volume_ratio:.1f}，缩量或过度放量")

    alignment_scores = {"bullish": 4, "mixed": 2, "bearish": 0, "unknown": 2}
    score += alignment_scores.get(enrichment.ma_alignment, 2)
    evidence.append(f"均线结构：{enrichment.ma_alignment}")
    return ScoreBreakdownItem(
        name="涨停前走势结构",
        score=min(15, score),
        max_score=15,
        evidence=evidence,
    )


def _score_sector_and_relay(
    enrichment: FirstBoardEnrichmentSnapshot | None,
) -> ScoreBreakdownItem:
    """Score sector breadth, hierarchy and prior-day promotion rate."""

    if enrichment is None:
        return ScoreBreakdownItem(
            name="板块强度与接力",
            score=4,
            max_score=8,
            evidence=["板块梯队快照缺失，按中性分处理"],
        )
    score = 0.0
    evidence = [
        f"行业首板 {enrichment.industry_first_board_count} 只、连板 "
        f"{enrichment.industry_continued_board_count} 只、炸板 {enrichment.industry_failed_count} 只"
    ]
    if enrichment.industry_first_board_count >= 3:
        score += 2
    elif enrichment.industry_first_board_count >= 2:
        score += 1
    if enrichment.industry_continued_board_count >= 1:
        score += 2
    if enrichment.industry_first_limit_rank is not None:
        if enrichment.industry_first_limit_rank <= 2:
            score += 2
        elif enrichment.industry_first_limit_rank <= 4:
            score += 1
        evidence.append(f"该股在行业首板中封板顺序第 {enrichment.industry_first_limit_rank}")
    promotion_rate = enrichment.previous_first_board_promotion_rate
    if promotion_rate is not None:
        if promotion_rate >= 0.15:
            score += 2
        elif promotion_rate >= 0.08:
            score += 1
        evidence.append(f"昨日首板今日晋级率 {promotion_rate:.0%}")
    return ScoreBreakdownItem(
        name="板块强度与接力",
        score=min(8, score),
        max_score=8,
        evidence=evidence,
    )


def _score_profile_and_history(
    enrichment: FirstBoardEnrichmentSnapshot | None,
) -> ScoreBreakdownItem:
    """Score tradable float size and recent limit-up frequency."""

    if enrichment is None:
        return ScoreBreakdownItem(
            name="流通盘与近期股性",
            score=2.5,
            max_score=5,
            evidence=["流通盘与近期涨停数据缺失，按中性分处理"],
        )
    score = 0.0
    evidence: list[str] = []
    market_cap = enrichment.float_market_cap
    if market_cap is not None:
        cap_yi = market_cap / 100_000_000
        if cap_yi <= 50:
            score += 3
            cap_label = "低市值，短线弹性相对较高"
        elif cap_yi <= 100:
            score += 2.5
            cap_label = "中小市值"
        elif cap_yi <= 200:
            score += 2
            cap_label = "中等市值"
        elif cap_yi <= 500:
            score += 1
            cap_label = "市值偏高"
        else:
            score += 0.5
            cap_label = "高市值，短线弹性相对受限"
        evidence.append(f"估算流通市值 {cap_yi:.1f} 亿元，{cap_label}")
    count_20d = enrichment.recent_limit_up_count_20d
    if 2 <= count_20d <= 4:
        score += 2
    elif count_20d == 1 or count_20d == 5:
        score += 1
    evidence.append(f"近 20 个交易日 K 线识别涨停 {count_20d} 次")
    return ScoreBreakdownItem(
        name="流通盘与近期股性",
        score=min(5, score),
        max_score=5,
        evidence=evidence,
    )


def _score_dragon_tiger(
    enrichment: FirstBoardEnrichmentSnapshot | None,
) -> ScoreBreakdownItem:
    """Use Dragon-Tiger List flow as a small after-close confirmation factor."""

    if enrichment is None:
        return ScoreBreakdownItem(
            name="龙虎榜资金",
            score=1.5,
            max_score=3,
            evidence=["龙虎榜快照缺失，按中性分处理"],
        )
    if not enrichment.dragon_tiger_on_list:
        return ScoreBreakdownItem(
            name="龙虎榜资金",
            score=1.5,
            max_score=3,
            evidence=["当日未上龙虎榜，不作正负判断"],
        )
    net_buy = enrichment.dragon_tiger_net_buy_amount
    if net_buy is None:
        score = 1.5
    elif net_buy > 0:
        score = 3
    else:
        score = 0.5
    evidence = [
        f"龙虎榜净买额 {net_buy / 100_000_000:+.2f} 亿元"
        if net_buy is not None
        else "已上龙虎榜，但净买额缺失"
    ]
    if enrichment.dragon_tiger_reason:
        evidence.append(enrichment.dragon_tiger_reason)
    return ScoreBreakdownItem(name="龙虎榜资金", score=score, max_score=3, evidence=evidence)


def _score_popularity(
    enrichment: FirstBoardEnrichmentSnapshot | None,
) -> ScoreBreakdownItem:
    """Score attention confirmation while limiting crowding bias."""

    if enrichment is None or any(
        item in enrichment.data_missing
        for item in ("popularity_snapshot", "eastmoney_popularity")
    ):
        return ScoreBreakdownItem(
            name="市场人气",
            score=2,
            max_score=4,
            evidence=["人气快照缺失，按中性分处理"],
        )
    rank = enrichment.popularity_rank
    if rank is None:
        return ScoreBreakdownItem(
            name="市场人气",
            score=1.5,
            max_score=4,
            evidence=["未进入已接入的人气榜单"],
        )
    if rank <= 5:
        score = 2.5
        label = "关注度极高，同时存在拥挤风险"
    elif rank <= 20:
        score = 3
        label = "关注度较高"
    elif rank <= 50:
        score = 2.5
        label = "具备一定关注度"
    else:
        score = 2
        label = "进入人气 Top100"
    if enrichment.popularity_rank_change is not None and enrichment.popularity_rank_change >= 20:
        score += 1
        label += "，排名快速上升"
    return ScoreBreakdownItem(
        name="市场人气",
        score=min(4, score),
        max_score=4,
        evidence=[f"收盘后人气排名第 {rank}，{label}"],
    )


def _score_first_limit_time(
    value: time,
    *,
    is_one_word_board: bool = False,
) -> ScoreBreakdownItem:
    """Score intraday seals above inaccessible one-word limit-up boards."""

    minutes = value.hour * 60 + value.minute
    if is_one_word_board:
        score, label = 10, "竞价封死的一字板，缺少盘中换手与承接验证"
    elif minutes <= 9 * 60 + 45:
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
    missing = set(facts.data_missing)
    if "enrichment_snapshot" in missing:
        confidence -= 0.15
    if "kline_20d" in missing:
        confidence -= 0.12
    if "popularity_snapshot" in missing or "eastmoney_popularity" in missing:
        confidence -= 0.04
    if "listing_date" in missing:
        confidence -= 0.03
    if "float_market_cap" in missing:
        confidence -= 0.05
    if "limit_up_history_60d" in missing:
        confidence -= 0.05

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
    if facts.first_limit_time <= time(9, 45) and not facts.is_one_word_board:
        reasons.append("首封时间较早")
    if facts.break_count == 0:
        reasons.append("炸板次数为 0")
    if 3 <= facts.turnover_rate <= 12:
        reasons.append("换手率处于相对适中区间")
    if facts.same_industry_limit_up_count >= 3:
        reasons.append("同行业涨停扩散较好")
    if facts.market_max_board_height >= 4:
        reasons.append("市场仍有连板高度")
    enrichment = facts.enrichment
    if enrichment and enrichment.ma_alignment == "bullish":
        reasons.append("涨停前均线呈多头排列")
    if enrichment and enrichment.industry_continued_board_count >= 1:
        reasons.append("所属行业存在连板梯队")
    if enrichment and enrichment.dragon_tiger_net_buy_amount is not None and enrichment.dragon_tiger_net_buy_amount > 0:
        reasons.append("龙虎榜呈净买入")
    if enrichment and enrichment.popularity_rank is not None and enrichment.popularity_rank <= 20:
        reasons.append("东方财富人气排名靠前")
    if (
        enrichment
        and enrichment.float_market_cap is not None
        and enrichment.float_market_cap <= 5_000_000_000
    ):
        reasons.append("流通市值较低，短线弹性相对较高")

    return (reasons or ["首板基础条件满足，但优势信号不突出"])[:7]


def _build_risks(facts: FirstBoardCandidateFacts) -> list[str]:
    """Build risk labels without turning them into trading advice."""

    risks: list[str] = []
    if facts.data_missing:
        risks.append(f"缺失字段：{', '.join(facts.data_missing)}")
    if facts.is_one_word_board:
        risks.append("一字板缺少盘中换手与承接验证")
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
    enrichment = facts.enrichment
    if enrichment and enrichment.return_20d_pct is not None and enrichment.return_20d_pct > 40:
        risks.append("近 20 日累计涨幅较高，存在过热风险")
    if enrichment and enrichment.recent_limit_up_count_20d >= 5:
        risks.append("近期涨停频繁，情绪拥挤度偏高")
    if enrichment and enrichment.float_market_cap is not None:
        cap_yi = enrichment.float_market_cap / 100_000_000
        if cap_yi > 500:
            risks.append("流通市值较高，短线弹性可能受限")
    if enrichment and enrichment.dragon_tiger_net_buy_amount is not None and enrichment.dragon_tiger_net_buy_amount < 0:
        risks.append("龙虎榜呈净卖出")
    if enrichment and enrichment.popularity_rank is not None and enrichment.popularity_rank <= 5:
        risks.append("人气排名极高，需关注交易拥挤")
    if (
        enrichment
        and enrichment.previous_first_board_promotion_rate is not None
        and enrichment.previous_first_board_promotion_rate < 0.08
    ):
        risks.append("昨日首板晋级率偏低")

    return (risks or ["未触发明显风险标签"])[:7]


def _is_one_word_board(event: LimitUpEvent) -> bool:
    """Infer an unbroken one-word board from auction seal facts."""

    auction_time = time(9, 25)
    return (
        event.closed_limit
        and event.break_count == 0
        and event.seal_count == 1
        and event.first_limit_time == auction_time
        and event.last_limit_time == auction_time
    )


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
