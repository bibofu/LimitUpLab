"""Deterministic routing for the bounded complex-query allowlist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ComplexityDecision(BaseModel):
    """Explain whether the existing fast path or bounded graph should run."""

    model_config = ConfigDict(frozen=True)

    route: Literal["fast", "complex"]
    reason_codes: tuple[str, ...]
    supported_scenario: str | None = None
    reason: str
    matched_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioRule:
    scenario: str
    required_signals: frozenset[str]
    reason: str
    reason_codes: tuple[str, ...] = ("observation_dependent", "bounded_replan_v2")
    optional_signals: frozenset[str] = frozenset()
    forbidden_signals: frozenset[str] = frozenset()
    priority: int = 0

    def matches(self, signals: frozenset[str]) -> bool:
        return self.required_signals <= signals and not self.forbidden_signals & signals


def extract_explicit_stock_target(message: str, explicit_symbol: str | None = None) -> str | None:
    """Extract only a target explicitly present in the request or request field."""

    if explicit_symbol and explicit_symbol.strip():
        return explicit_symbol.strip()
    compact = "".join(message.split())
    code = re.search(r"(?<!\d)\d{6}(?!\d)", compact)
    if code:
        return code.group(0)
    for pattern in (
        r"(?:查|查询|看看|分析)([\u4e00-\u9fff]{2,8}?)(?:最近|近期|\d{1,2}(?:日|天))?(?:k线|龙虎榜)",
        r"(?:查|查询|看看|分析)([\u4e00-\u9fff]{2,8}?)(?:最近|近期).{0,6}?新闻",
        r"([\u4e00-\u9fff]{2,8}?)(?:最近|近期).{0,6}?新闻",
    ):
        match = re.search(pattern, compact)
        if match:
            candidate = match.group(1).strip()
            if candidate not in {"一下", "个股", "这只股票", "该股票", "股票"}:
                return candidate
    return None


def _signals(message: str) -> frozenset[str]:
    compact = "".join(message.lower().split())
    codes = re.findall(r"(?<!\d)\d{6}(?!\d)", compact)
    found: set[str] = set()
    if any(term in compact for term in ("热股", "人气榜", "人气排名")):
        found.add("hot_scope")
    if "涨停" in compact:
        found.add("limit_up_scope")
    if any(term in compact for term in ("交集", "同时", "里面")):
        found.add("intersection")
    if any(term in compact for term in ("首板评分", "首板评级", "评分", "评级")):
        found.add("rating")
    if any(term in compact for term in ("评分最高", "最高分首板", "评分前")):
        found.add("top_ratings")
    if any(term in compact for term in ("k线", "走势")):
        found.add("kline")
    if any(term in compact for term in ("逐只", "分别", "每只")):
        found.add("per_candidate")
    if "龙虎榜" in compact:
        found.add("dragon_tiger")
    if any(term in compact for term in ("没有龙虎榜", "龙虎榜为空", "未上榜")) or re.search(
        r"龙虎榜.{0,8}(?:没有结果|无结果|查不到|为空)", compact
    ):
        found.add("empty_dragon_tiger_branch")
    if "新闻" in compact:
        found.add("news")
    if any(term in compact for term in ("没有结果", "结果为空", "查不到", "没有新闻")):
        found.add("empty_news_branch")
    if extract_explicit_stock_target(message):
        found.add("explicit_stock_target")
    if len(set(codes)) >= 2:
        found.add("multiple_stock_targets")
    if any(term in compact for term in ("即使一只失败", "一只失败也", "失败也继续", "继续比较")):
        found.add("continue_after_failure")
    return frozenset(found)


_FLAGSHIP_RULE = ScenarioRule(
    scenario="hot_limit_up_rating_intersection_v1",
    required_signals=frozenset({"hot_scope", "limit_up_scope", "intersection", "rating"}),
    reason="hot-stock and limit-up intersection produces a dynamic rating input set",
    reason_codes=("dynamic_entity_set", "dependent_tool_arguments"),
    forbidden_signals=frozenset({"kline", "dragon_tiger"}),
    priority=100,
)

_PHASE_TWO_RULES = (
    ScenarioRule(
        "intersection_risk_v2",
        frozenset({
            "hot_scope", "limit_up_scope", "intersection", "rating", "kline",
            "dragon_tiger", "empty_dragon_tiger_branch", "news",
        }),
        "intersection members require ratings, trend and conditional risk evidence",
        priority=120,
    ),
    ScenarioRule(
        "stock_risk_branch_v2",
        frozenset({
            "explicit_stock_target", "kline", "dragon_tiger",
            "empty_dragon_tiger_branch", "news",
        }),
        "an explicit stock requires K-line and Dragon-Tiger evidence with conditional news fallback",
        priority=110,
    ),
    ScenarioRule(
        "rating_dragon_tiger_branch_v2",
        frozenset({"top_ratings", "dragon_tiger", "empty_dragon_tiger_branch"}),
        "top-rated candidates require an outcome-dependent Dragon-Tiger fallback",
        priority=90,
    ),
    ScenarioRule(
        "top_ratings_then_kline_v2",
        frozenset({"top_ratings", "kline", "per_candidate"}),
        "observed top-rated candidates become downstream K-line arguments",
        priority=80,
    ),
    ScenarioRule(
        "empty_news_fallback_v2",
        frozenset({"news", "empty_news_branch", "kline", "explicit_stock_target"}),
        "an explicitly identified stock requires fallback evidence when news is empty",
        priority=80,
    ),
    ScenarioRule(
        "partial_stock_comparison_v2",
        frozenset({"multiple_stock_targets", "kline", "continue_after_failure"}),
        "multi-stock comparison must preserve successes and retry only failed candidates",
        priority=80,
    ),
)


def route_complexity(message: str) -> ComplexityDecision:
    """Route only allowlisted observation-dependent graph scenarios."""

    signals = _signals(message)
    for rule in sorted((_FLAGSHIP_RULE, *_PHASE_TWO_RULES), key=lambda item: item.priority, reverse=True):
        if rule.matches(signals):
            return ComplexityDecision(
                route="complex",
                reason_codes=rule.reason_codes,
                supported_scenario=rule.scenario,
                reason=rule.reason,
                matched_signals=tuple(sorted(rule.required_signals)),
            )
    return ComplexityDecision(
        route="fast",
        reason_codes=("fast_path_default",),
        reason="no allowlisted complex scenario matched",
        matched_signals=tuple(sorted(signals)),
    )
