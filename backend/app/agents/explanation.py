"""LLM-backed explanation agent with deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import FirstBoardRating
from app.services.llm_provider import LLMProvider, get_llm_provider


EXPLANATION_VERSION = "first-board-explanation-v2"
FORBIDDEN_TERMS = ("买入", "卖出", "仓位", "目标价", "收益承诺")
SAFETY_BOUNDARY = "不构成买卖建议"


@dataclass(frozen=True)
class ExplanationResult:
    """Generated explanation plus trace metadata."""

    answer: str
    source: str
    tool_calls: list[str]
    warnings: list[str]


def explain_first_board_rating(
    rating: FirstBoardRating,
    provider: LLMProvider | None = None,
) -> ExplanationResult:
    """Explain a first-board rating using facts and optional LLM generation."""

    active_provider = provider or get_llm_provider()
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(rating)

    try:
        result = active_provider.generate(system_prompt, user_prompt)
        if _contains_forbidden_terms(result.content):
            return _template_result(
                rating,
                warning="LLM output failed safety validation; template fallback used.",
            )
        return ExplanationResult(
            answer=_ensure_safety_boundary(result.content),
            source=f"{result.provider}:{result.model}",
            tool_calls=["llm_explanation"],
            warnings=[],
        )
    except Exception as error:
        return _template_result(
            rating,
            warning=f"LLM unavailable; template fallback used: {error}",
        )


def _template_result(
    rating: FirstBoardRating,
    warning: str,
) -> ExplanationResult:
    """Build a deterministic explanation when LLM generation is unavailable."""

    facts = rating.facts
    breakdown = "；".join(
        f"{item.name}{item.score:.1f}/{item.max_score:.0f}"
        for item in rating.score_breakdown[:5]
    )
    answer = (
        f"{facts.trade_date.isoformat()} {facts.name}({facts.symbol}) "
        f"的首板评级为 {rating.rating}，"
        f"评分 {rating.score:.1f}，置信度 {rating.confidence:.0%}。\n"
        f"支持因素：{'；'.join(rating.reasons[:4])}。\n"
        f"评分拆解：{breakdown}。\n"
        f"风险观察：{'；'.join(rating.risks[:4])}。\n"
        f"数据限制：当前解释只基于本地涨停事件和评分拆解，不包含实时新闻或公告。"
        f"{SAFETY_BOUNDARY}。"
    )
    return ExplanationResult(
        answer=answer,
        source="template",
        tool_calls=["template_explanation"],
        warnings=[warning],
    )


def _build_system_prompt() -> str:
    """Build the fixed Explanation Agent system prompt."""

    return (
        "You are an explanation agent for an A-share first-board rating system. "
        "Use only the provided structured facts. Do not invent market data, news, "
        "fund flow, fundamentals, target prices, position sizing, or trading orders. "
        "Explain the score, supporting evidence, risks, and data limits. "
        "Write in Chinese. Always say the analysis is not investment advice."
    )


def _build_user_prompt(
    rating: FirstBoardRating,
) -> str:
    """Build a compact facts-only prompt for the LLM."""

    facts = rating.facts
    breakdown = [
        {
            "name": item.name,
            "score": item.score,
            "max_score": item.max_score,
            "evidence": item.evidence,
        }
        for item in rating.score_breakdown
    ]
    return (
        f"Stock: {facts.name}({facts.symbol})\n"
        f"Trade date: {facts.trade_date.isoformat()}\n"
        f"Rating: {rating.rating}, score: {rating.score}, confidence: {rating.confidence}\n"
        f"Facts: first_limit={facts.first_limit_time}, break_count={facts.break_count}, "
        f"seal_count={facts.seal_count}, amount={facts.amount}, turnover={facts.turnover_rate}, "
        f"industry={facts.industry}, concept={facts.concept}, "
        f"same_industry_limit_up_count={facts.same_industry_limit_up_count}, "
        f"market_failed_limit_up_rate={facts.market_failed_limit_up_rate}\n"
        f"Score breakdown: {breakdown}\n"
        f"Reasons: {rating.reasons}\n"
        f"Risks: {rating.risks}\n"
    )


def _contains_forbidden_terms(content: str) -> bool:
    """Return whether generated content crosses product safety boundaries."""

    return any(term in content for term in FORBIDDEN_TERMS)


def _ensure_safety_boundary(content: str) -> str:
    """Append the product boundary when the model omitted it."""

    if SAFETY_BOUNDARY in content:
        return content
    return f"{content.rstrip()}\n{SAFETY_BOUNDARY}。"
