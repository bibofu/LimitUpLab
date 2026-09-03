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
    examples: tuple[str, ...] = ()
    answer_guidance: str = ""

    def planner_payload(self) -> dict[str, Any]:
        """Return the compact capability schema embedded in the planner prompt."""

        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "required_evidence": [item.name for item in self.required_tools],
        }
        if self.examples:
            payload["examples"] = list(self.examples)
        return payload


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
        examples=("今天市场环境如何", "今天 A 股整体怎么样", "总结一下最新盘面情况"),
        answer_guidance=(
            "按大盘指数、涨跌停结构、板块强弱、热门个股、客观总结五部分回答。"
            "指数包含最新日涨跌和近 5 个交易日表现；涨跌停结构包含首板、连板、未回封、"
            "跌停和最高板，缺失字段明确说明；板块列涨幅前 5 和跌幅前 5；热股列前 5 名及"
            "可用的最新涨跌。分别注明收盘、板块和人气数据时点，不用单一情绪标签代替事实。"
        ),
    ),
    AgentCapability(
        "market_index_trend",
        "只查询大盘曲线或主要指数在指定交易日窗口内的走势，不扩展到全景市场综述。",
        (CapabilityToolRequirement("market_index_trend", {"days": 5}),),
    ),
    AgentCapability(
        "sector_performance",
        "查询全市场行业强弱榜，或指定行业、概念板块的行情表现。",
        (CapabilityToolRequirement("sector_performance", {"sector": None}),),
    ),
    AgentCapability(
        "sector_stock_ranking",
        "查询一个明确行业或概念板块中，哪些成分股近期已发生的日K线趋势相对更强。",
        (
            CapabilityToolRequirement(
                "sector_stock_ranking", {"days": 20, "limit": 10}
            ),
        ),
        examples=(
            "游戏板块哪些股票走势好",
            "半导体板块近期强势股有哪些",
            "软件行业近20日走势排名",
        ),
        answer_guidance=(
            "注明解析到的同花顺行业或概念名称、数据截止日和成分股覆盖率；按工具顺序列出"
            "名次、股票名称与代码、趋势分、近5/20日涨跌、均线趋势、量比和回撤，缺失项"
            "明确省略。趋势分只用于比较已经发生的价格量能结构，不解释为未来上涨概率。"
        ),
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
        examples=("有哪些票比较热门", "热股榜前20名", "同花顺人气榜有哪些股票"),
        answer_guidance=(
            "未指定数量时展示前 20 名，指定 Top-N 时严格遵守数量；按事实排名输出名次、名称"
            "和六位代码，不重新排序。注明真实数据源、北京时间采集时间和实际返回数量。"
            "人气只代表关注度和拥挤度，不解释为评分、交易信号或上涨概率。"
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
        examples=("最新的财经新闻", "最新的新闻", "今天有什么财经快讯"),
        answer_guidance=(
            "未指定范围时总结最近 48 小时且最多 8 条，注明北京时间抓取时间和实际成功来源。"
            "每条保留发布时间、来源、标题、简短摘要和原文链接；新闻事实与可能影响分开，"
            "影响只能标记为推断。无有效结果时明确无法获取，不用模型记忆补充。"
        ),
    ),
    AgentCapability(
        "stock_news",
        "查询一只明确股票最近的新闻、公告类报道或监管消息，要求保留来源、发布时间和链接。",
        (CapabilityToolRequirement("stock_news", {"days": 7, "limit": 10}),),
        examples=("中电鑫龙最近有什么新闻", "600519 有新消息吗", "它最近有公告吗"),
        answer_guidance=(
            "先确认唯一股票实体；默认查询近 7 个自然日、最多 10 条并按发布时间倒序。"
            "注明股票名称和代码、抓取时间、缓存状态；每条保留发布时间、来源、类型、标题、"
            "摘要和链接。媒体报道不得写成正式公告，没有直接相关结果时不以综合新闻补足。"
        ),
    ),
    AgentCapability(
        "stock_activity",
        "综合查询一只明确股票最近发生了什么或有何动态，包括收盘走势、涨停记录、评分补充事实和个股新闻。",
        (
            CapabilityToolRequirement(
                "stock_activity",
                {"days": 7, "news_limit": 8},
            ),
        ),
    ),
    AgentCapability(
        "market_events",
        "查询完整交易日的涨停、跌停或炸板市场事件名单、数量及常用筛选结果。",
        (
            CapabilityToolRequirement(
                "market_event_pool", {"event_type": "limit_up", "limit": 30}
            ),
        ),
        examples=(
            "今天跌停的票有哪些",
            "列出最新跌停名单",
            "今天哪些股票涨停",
            "炸板未回封的股票有几只",
        ),
        answer_guidance=(
            "严格遵守用户要求的事件类型、日期、市场和数量；先报告匹配数量，再按返回顺序列出"
            "名称和代码，可用时补充涨跌幅或行业。不得把跌停查询改成涨停查询，也不得用涨停"
            "或炸板事实代替跌停名单。未指定日期时使用最新完整交易日。"
        ),
    ),
    AgentCapability(
        "limit_up_pool",
        "查询某个交易日的涨停、首板、连板或炸板名单；不用于跨日晋级数量和比例。",
        (CapabilityToolRequirement("limit_up_events"),),
        examples=("首板票有哪些", "今天二连板有哪些", "创业板涨停股票", "列出炸板未回封股票"),
        answer_guidance=(
            "严格保留用户指定的日期、市场、板数、状态、题材、排序和数量条件；未指定日期时"
            "使用最新完整交易日并写明 ISO 日期。先报告匹配数量，再按事实顺序列名称和代码。"
            "用户要求全部时逐只完整返回且去重，严格区分未回封炸板与开板后重新封住。"
        ),
    ),
    AgentCapability(
        "first_board_rating",
        "查询首板一进二观察候选、Top10 评级、排名、评分解释、位置和风险。",
        (CapabilityToolRequirement("first_board_ratings"),),
        examples=("哪些首板候选评分靠前", "为什么这只股票评分高", "首板评级前10名"),
        answer_guidance=(
            "未指定日期时使用最新完整交易日并写明 ISO 日期。排名默认最多展示 10 只，包含"
            "名次、名称、代码、评级、分数和主要依据；解释单只股票时区分基本信息、因子得分、"
            "支持证据、风险和缺失输入。位置、封板事实和分项得分必须忠于记录，结果称为研究"
            "评级，不表述为确定性预测。"
        ),
    ),
    AgentCapability(
        "first_board_discovery",
        "查询低位挖掘观察池；按热门题材和新闻催化召回，再用财报与近 60 日 K 线位置、量能和趋势修复验证，并分别解释三类证据。",
        (CapabilityToolRequirement("first_board_discovery"),),
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
TOOL_CAPABILITIES = {
    requirement.name: capability.name
    for capability in CAPABILITIES
    for requirement in capability.required_tools
    if len(capability.required_tools) == 1
}


def normalize_capabilities(
    raw_capabilities: object,
    *,
    tool_calls: Iterable[dict[str, Any]] = (),
) -> tuple[str, ...]:
    """Normalize planner output and infer capability IDs from selected tools."""

    requested: list[str] = []
    if isinstance(raw_capabilities, list):
        for item in raw_capabilities:
            raw_name = item.get("name") if isinstance(item, dict) else item
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip().lower().replace("-", "_")
            if name in CAPABILITY_BY_NAME and name not in requested:
                requested.append(name)

    for call in tool_calls:
        tool_name = str(call.get("name") or "")
        capability_name = TOOL_CAPABILITIES.get(tool_name)
        if capability_name and capability_name not in requested:
            requested.append(capability_name)
    return tuple(requested)


def infer_capabilities_from_facts(
    capabilities: Iterable[str],
    facts: dict[str, Any],
) -> tuple[str, ...]:
    """Recover capabilities when policy-repaired tools produced the evidence."""

    resolved = list(normalize_capabilities(list(capabilities)))
    fact_names = set(facts)
    covered_by_composite = {
        requirement.name
        for name in resolved
        if name in CAPABILITY_BY_NAME
        and len(CAPABILITY_BY_NAME[name].required_tools) > 1
        for requirement in CAPABILITY_BY_NAME[name].required_tools
    }
    for capability in sorted(
        CAPABILITIES,
        key=lambda item: len(item.required_tools),
        reverse=True,
    ):
        if capability.name in resolved:
            continue
        if (
            len(capability.required_tools) == 1
            and capability.required_tools[0].name in covered_by_composite
        ):
            continue
        if capability.required_tools and all(
            requirement.name in fact_names for requirement in capability.required_tools
        ):
            resolved.append(capability.name)
            if len(capability.required_tools) > 1:
                covered_by_composite.update(
                    requirement.name for requirement in capability.required_tools
                )
    return tuple(resolved)


def capability_answer_instruction(capabilities: Iterable[str]) -> str:
    """Build answer guidance only for capabilities active in this request."""

    selected = [
        CAPABILITY_BY_NAME[name]
        for name in dict.fromkeys(capabilities)
        if name in CAPABILITY_BY_NAME
    ]
    covered_by_composite = {
        requirement.name
        for capability in selected
        if len(capability.required_tools) > 1
        for requirement in capability.required_tools
    }
    guidance = [
        f"- {capability.name}: {capability.answer_guidance}"
        for capability in selected
        if capability.answer_guidance
        and not (
            len(capability.required_tools) == 1
            and capability.required_tools[0].name in covered_by_composite
        )
    ]
    if not guidance:
        return ""
    return " CAPABILITY_RESPONSE_CONTRACTS:\n" + "\n".join(guidance)


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


def available_capability_names(
    allowed_tool_names: set[str] | frozenset[str],
) -> tuple[str, ...]:
    """Return capability IDs whose complete evidence contract is available."""

    return tuple(
        item.name
        for item in CAPABILITIES
        if all(req.name in allowed_tool_names for req in item.required_tools)
    )
