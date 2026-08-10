"""Critic review for explainable first-board ratings."""

from datetime import date, time

from app.agents.first_board import build_first_board_ratings
from app.models import (
    FirstBoardCriticResponse,
    FirstBoardRating,
    LimitUpEvent,
    SimilarFirstBoardCase,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.similar_cases import find_similar_first_board_cases


CRITIC_VERSION = "first-board-critic-rule-v1"


def build_first_board_critic(
    events: list[LimitUpEvent],
    symbol: str,
    trade_date: date | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    similar_limit: int = 5,
) -> FirstBoardCriticResponse:
    """Review one first-board rating and surface opposing evidence."""

    ratings = build_first_board_ratings(events=events, trade_date=trade_date)
    rating = next(
        (item for item in ratings.candidates if item.facts.symbol == symbol),
        None,
    )
    if rating is None:
        raise ValueError("target first-board rating not found")

    repository = first_board_repository or SQLiteFirstBoardRepository()
    similar_cases: list[SimilarFirstBoardCase] = []
    try:
        similar_response = find_similar_first_board_cases(
            symbol=rating.facts.symbol,
            trade_date=rating.facts.trade_date,
            repository=repository,
            limit=similar_limit,
        )
        similar_cases = similar_response.cases
    except ValueError:
        similar_cases = []

    support = _build_support_evidence(rating, similar_cases)
    counter = _build_counter_evidence(rating, similar_cases)
    missing = list(rating.facts.data_missing)
    warnings = _build_critic_warnings(rating, similar_cases)
    suggested_confidence = _suggest_confidence(rating, counter, missing, similar_cases)
    delta = round(suggested_confidence - rating.confidence, 2)

    return FirstBoardCriticResponse(
        symbol=rating.facts.symbol,
        name=rating.facts.name,
        trade_date=rating.facts.trade_date,
        rating=rating.rating,
        score=rating.score,
        original_confidence=rating.confidence,
        suggested_confidence=suggested_confidence,
        confidence_delta=delta,
        verdict=_verdict(delta, counter),
        support_evidence=support,
        counter_evidence=counter,
        missing_data=missing,
        critic_warnings=warnings,
        review_questions=_build_review_questions(rating, counter, missing, similar_cases),
        similar_case_count=len(similar_cases),
        similar_case_outcome_ready_count=sum(
            1 for item in similar_cases if item.outcome and item.outcome.outcome_ready
        ),
        generated_by=CRITIC_VERSION,
    )


def _build_support_evidence(
    rating: FirstBoardRating,
    similar_cases: list[SimilarFirstBoardCase],
) -> list[str]:
    """Collect facts that support the current score."""

    facts = rating.facts
    evidence: list[str] = []
    if facts.closed_limit and facts.break_count == 0:
        evidence.append("收盘封住且盘中未炸板，封板稳定性是正向证据。")
    if facts.first_limit_time <= time(10, 30):
        evidence.append(f"首封时间 {facts.first_limit_time.strftime('%H:%M')}，时间位置相对靠前。")
    if 3 <= facts.turnover_rate <= 12:
        evidence.append(f"换手率 {facts.turnover_rate:.1f}%，处于评分规则的适中区间。")
    if facts.same_industry_limit_up_count >= 3:
        evidence.append(f"同业涨停 {facts.same_industry_limit_up_count} 只，存在板块扩散。")
    if facts.market_max_board_height >= 4:
        evidence.append(f"市场最高连板 {facts.market_max_board_height} 板，短线高度仍在。")

    promoted = [
        item
        for item in similar_cases
        if item.outcome and item.outcome.outcome_ready and item.outcome.promoted_to_second_board
    ]
    if promoted:
        evidence.append(f"历史相似案例中有 {len(promoted)} 个样本晋级二板。")

    return evidence or ["当前评分有基础 facts 支撑，但正向证据不突出。"]


def _build_counter_evidence(
    rating: FirstBoardRating,
    similar_cases: list[SimilarFirstBoardCase],
) -> list[str]:
    """Collect facts that challenge the current score."""

    facts = rating.facts
    evidence: list[str] = []
    if rating.confidence < 0.75:
        evidence.append(f"原始置信度仅 {rating.confidence:.0%}，评分可靠性需要打折。")
    if facts.data_missing:
        evidence.append(f"缺失字段：{', '.join(facts.data_missing)}。")
    if facts.break_count > 0:
        evidence.append(f"盘中炸板 {facts.break_count} 次，说明承接并非单边稳定。")
    if facts.seal_count >= 4:
        evidence.append(f"封板次数 {facts.seal_count} 次，回封压力偏高。")
    if facts.turnover_rate > 18 or facts.turnover_rate < 1:
        evidence.append(f"换手率 {facts.turnover_rate:.1f}%，偏离适中样本区间。")
    if facts.amount > 8_000_000_000:
        evidence.append(f"成交额 {facts.amount / 100_000_000:.1f} 亿，资金消耗偏大。")
    if facts.market_failed_limit_up_rate >= 0.55:
        evidence.append(f"市场炸板率 {facts.market_failed_limit_up_rate:.0%}，接力环境偏弱。")
    elif facts.market_failed_limit_up_rate >= 0.35:
        evidence.append(f"市场炸板率 {facts.market_failed_limit_up_rate:.0%}，分歧偏高。")
    if facts.same_industry_limit_up_count <= 1:
        evidence.append("同业涨停扩散不足，题材合力需要谨慎确认。")

    ready_cases = [
        item for item in similar_cases if item.outcome and item.outcome.outcome_ready
    ]
    weak_cases = [
        item
        for item in ready_cases
        if (item.outcome and item.outcome.three_day_close_pct is not None and item.outcome.three_day_close_pct < 0)
    ]
    if ready_cases and len(weak_cases) >= max(1, len(ready_cases) // 2):
        evidence.append(
            f"可用历史相似样本 {len(ready_cases)} 个，其中 {len(weak_cases)} 个三日收盘表现为负。"
        )
    if similar_cases and not ready_cases:
        evidence.append("历史相似案例缺少首板后走势缓存，无法验证相似样本表现。")

    return evidence or ["未发现足以显著反驳当前评分的结构化证据。"]


def _build_critic_warnings(
    rating: FirstBoardRating,
    similar_cases: list[SimilarFirstBoardCase],
) -> list[str]:
    """Build concise warnings for the frontend and chat answer."""

    warnings = [
        "Critic 只调整置信度解释，不修改原始 score。",
        "当前结论基于本地结构化行情，不包含实时新闻、公告或资金流。",
    ]
    if rating.facts.data_missing:
        warnings.append("候选 facts 存在缺失字段，建议降低对评分的依赖。")
    if not similar_cases:
        warnings.append("未取得历史相似案例，缺少横向案例验证。")
    return warnings


def _suggest_confidence(
    rating: FirstBoardRating,
    counter_evidence: list[str],
    missing_data: list[str],
    similar_cases: list[SimilarFirstBoardCase],
) -> float:
    """Calculate a critic-side confidence suggestion."""

    confidence = rating.confidence
    confidence -= 0.03 * min(len(counter_evidence), 5)
    confidence -= 0.04 * min(len(missing_data), 3)

    ready_cases = [
        item for item in similar_cases if item.outcome and item.outcome.outcome_ready
    ]
    if similar_cases and not ready_cases:
        confidence -= 0.06
    elif ready_cases:
        promoted_count = sum(
            1 for item in ready_cases if item.outcome and item.outcome.promoted_to_second_board
        )
        promotion_rate = promoted_count / len(ready_cases)
        if promotion_rate < 0.25:
            confidence -= 0.08
        elif promotion_rate >= 0.5:
            confidence += 0.03

    return round(max(0.35, min(0.95, confidence)), 2)


def _verdict(delta: float, counter_evidence: list[str]) -> str:
    """Map critic confidence adjustment to a display verdict."""

    if delta <= -0.15 or len(counter_evidence) >= 5:
        return "fragile"
    if delta <= -0.05 or len(counter_evidence) >= 3:
        return "cautious"
    return "supportive"


def _build_review_questions(
    rating: FirstBoardRating,
    counter_evidence: list[str],
    missing_data: list[str],
    similar_cases: list[SimilarFirstBoardCase],
) -> list[str]:
    """Suggest human review questions instead of trading instructions."""

    questions = [
        "评分中的最高分因子是否由可复核 facts 支撑？",
        "同题材首板是否具备持续扩散，而不是单票孤立表现？",
    ]
    if counter_evidence:
        questions.append("反向证据是否足以降低对当前评级的信任？")
    if missing_data:
        questions.append("缺失字段补齐后，置信度是否仍能维持？")
    if not similar_cases:
        questions.append("相似案例库缺失时，是否应该减少结论强度？")
    if rating.rating in {"A", "B"}:
        questions.append("高评级是否主要来自单一强项，还是多因子共同支持？")
    return questions[:5]
