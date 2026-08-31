"""Semantic capability contracts for planner-driven Agent workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class CapabilityToolRequirement:
    """One evidence tool required by a normalized Agent capability."""

    name: str
    default_arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCapability:
    """A wording-independent business capability exposed to the LLM planner."""

    name: str
    description: str
    required_tools: tuple[CapabilityToolRequirement, ...]

    def planner_payload(self) -> dict[str, Any]:
        """Return the compact capability schema embedded in the planner prompt."""

        return {
            "name": self.name,
            "description": self.description,
            "required_evidence": [item.name for item in self.required_tools],
        }


CAPABILITIES: tuple[AgentCapability, ...] = (
    AgentCapability(
        "market_environment",
        "综合说明最新市场环境；仅用于同时要求指数、涨跌停、行业、热股等多个维度的全景综述。",
        (
            CapabilityToolRequirement("market_summary", {"include_limit_down": True}),
            CapabilityToolRequirement("market_index_trend", {"days": 5}),
            CapabilityToolRequirement("sector_performance", {"sector": None}),
            CapabilityToolRequirement(
                "hot_stock_ranking",
                {
                    "period": "day",
                    "limit": 20,
                    "source": "auto",
                    "enrich_performance": True,
                },
            ),
        ),
    ),
    AgentCapability(
        "market_index_trend",
        "只查询大盘曲线或主要指数在指定交易日窗口内的走势，不扩展到全景市场综述。",
        (CapabilityToolRequirement("market_index_trend", {"days": 5}),),
    ),
    AgentCapability(
        "sector_performance",
        "查询全市场行业强弱榜或指定行业表现。",
        (CapabilityToolRequirement("sector_performance", {"sector": None}),),
    ),
    AgentCapability(
        "popularity",
        "查询当前热门股票、人气榜、关注度榜或指定 Top-N。",
        (
            CapabilityToolRequirement(
                "hot_stock_ranking",
                {"period": "day", "limit": 20, "source": "auto"},
            ),
        ),
    ),
    AgentCapability(
        "finance_news",
        "查询最新综合财经新闻和市场快讯；用户在金融项目中只说最新新闻、最近消息或新闻摘要且未限定公司/板块时也属于此能力。",
        (
            CapabilityToolRequirement(
                "finance_news", {"query": None, "limit": 8, "hours": 48}
            ),
        ),
    ),
    AgentCapability(
        "limit_up_pool",
        "查询某个交易日的涨停、首板、连板或炸板名单；不用于跨日晋级数量和比例。",
        (CapabilityToolRequirement("limit_up_events"),),
    ),
    AgentCapability(
        "first_board_rating",
        "查询首板一进二观察候选、Top10 评级、排名、评分解释、位置和风险。",
        (CapabilityToolRequirement("first_board_ratings"),),
    ),
    AgentCapability(
        "board_promotion",
        "查询前一交易日封板股票在次日继续连板的数量、比例，或首板到二板的跨日实现情况。",
        (CapabilityToolRequirement("daily_board_promotion", {"days": 5}),),
    ),
    AgentCapability(
        "stock_trend",
        "查询单只股票的 K 线、收益、均线、量价或回撤。",
        (CapabilityToolRequirement("stock_kline"),),
    ),
    AgentCapability(
        "dragon_tiger",
        "查询最新或指定交易日龙虎榜上榜股票、机构席位和游资资金事实。",
        (CapabilityToolRequirement("dragon_tiger_list", {"limit": 30}),),
    ),
    AgentCapability(
        "prediction_review",
        "复盘近期高分 Top10、后续走势、好坏样本特征及相对一进二成功率。",
        (CapabilityToolRequirement("review_high_score_picks"),),
    ),
    AgentCapability(
        "prediction_quality",
        "审计整体预测质量、样本完整性、统计基线和评分信号有效性；不列近期 Top10 个股结果。",
        (CapabilityToolRequirement("prediction_quality_audit"),),
    ),
    AgentCapability(
        "rating_backtest",
        "查询历史评分回测和失败样本。",
        (CapabilityToolRequirement("rating_backtest"),),
    ),
    AgentCapability(
        "rating_evaluation",
        "查询单日持久化预测结果和评价事实。",
        (CapabilityToolRequirement("rating_evaluation"),),
    ),
    AgentCapability(
        "scoring_policy",
        "查询评分策略、权重、Champion/Challenger 和自我改进状态。",
        (CapabilityToolRequirement("scoring_policy_status"),),
    ),
    AgentCapability(
        "rating_critic",
        "对单只首板评级执行支持证据与反对证据复核。",
        (CapabilityToolRequirement("first_board_critic"),),
    ),
    AgentCapability(
        "web_research",
        "对本地工具未覆盖的当前公开信息执行有来源的网页检索。",
        (CapabilityToolRequirement("web_search", {"limit": 5}),),
    ),
)

CAPABILITY_BY_NAME = {item.name: item for item in CAPABILITIES}
SKILL_CAPABILITIES = {
    "finance-news": "finance_news",
    "first-board-rating": "first_board_rating",
    "limit-up-pool": "limit_up_pool",
    "market-environment": "market_environment",
    "popularity": "popularity",
}
TOOL_CAPABILITIES = {
    requirement.name: capability.name
    for capability in CAPABILITIES
    for requirement in capability.required_tools
    if len(capability.required_tools) == 1
}


def normalize_capabilities(
    raw_capabilities: object,
    *,
    skill_name: object = None,
    tool_calls: Iterable[dict[str, Any]] = (),
) -> tuple[str, ...]:
    """Normalize planner output and infer missing capability IDs for compatibility."""

    requested: list[str] = []
    if isinstance(raw_capabilities, list):
        for item in raw_capabilities:
            raw_name = item.get("name") if isinstance(item, dict) else item
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip().lower().replace("-", "_")
            if name in CAPABILITY_BY_NAME and name not in requested:
                requested.append(name)

    if isinstance(skill_name, str):
        normalized_skill = skill_name.strip().lower().replace("_", "-")
        capability_name = SKILL_CAPABILITIES.get(normalized_skill)
        if capability_name and capability_name not in requested:
            requested.append(capability_name)

    for call in tool_calls:
        tool_name = str(call.get("name") or "")
        capability_name = TOOL_CAPABILITIES.get(tool_name)
        if capability_name and capability_name not in requested:
            requested.append(capability_name)
    return tuple(requested)


def ensure_capability_tool_calls(
    capabilities: Iterable[str],
    tool_calls: list[dict[str, Any]],
    *,
    allowed_tool_names: set[str] | frozenset[str],
    max_calls: int = 8,
) -> list[dict[str, Any]]:
    """Merge minimum evidence calls into a planner plan without duplicating tools."""

    normalized = [
        {
            "name": str(call.get("name") or ""),
            "arguments": dict(call.get("arguments") or {}),
        }
        for call in tool_calls
        if isinstance(call, dict) and call.get("name")
    ]
    by_name = {call["name"]: call for call in normalized}
    required_order: list[str] = []
    for capability_name in capabilities:
        capability = CAPABILITY_BY_NAME.get(capability_name)
        if capability is None:
            continue
        for requirement in capability.required_tools:
            if requirement.name not in allowed_tool_names:
                continue
            existing = by_name.get(requirement.name)
            if existing is None:
                existing = {
                    "name": requirement.name,
                    "arguments": dict(requirement.default_arguments),
                }
                by_name[requirement.name] = existing
                normalized.append(existing)
            else:
                existing["arguments"] = {
                    **requirement.default_arguments,
                    **existing["arguments"],
                }
            if requirement.name not in required_order:
                required_order.append(requirement.name)

    ordered = [by_name[name] for name in required_order]
    ordered.extend(call for call in normalized if call["name"] not in required_order)
    return ordered[:max_calls]


def capability_schema_prompt(
    allowed_tool_names: set[str] | frozenset[str],
) -> str:
    """Serialize capabilities whose evidence tools are available in this profile."""

    available = [
        item.planner_payload()
        for item in CAPABILITIES
        if all(req.name in allowed_tool_names for req in item.required_tools)
    ]
    return json.dumps(available, ensure_ascii=False, separators=(",", ":"))
