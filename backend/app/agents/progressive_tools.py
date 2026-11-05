"""Progressive disclosure of business tools through a searchable meta-tool.

The initial tool set stays deterministic. When the model needs a capability that
is not visible, it calls ``search_tools``; the runner adds matching tools to the
next model call instead of exposing the full catalog up front.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SEARCH_TOOLS_TOOL = "search_tools"
PROGRESSIVE_TOOLS_ENV = "LIMITUPLAB_PROGRESSIVE_TOOLS"
MAX_ACTIVE_TOOLS_ENV = "LIMITUPLAB_PROGRESSIVE_MAX_TOOLS"
DEFAULT_MAX_ACTIVE_TOOLS = 20
MODES = ("off", "hybrid", "full")
_MAX_ACTIVE_TOOLS = 25
_QUERY_SPLIT = re.compile(r"[\s,，、;；/]+")
_CJK = re.compile(r"[一-鿿]")

# A few capability words that do not appear verbatim in the short tool
# descriptions. The long schema text still participates in scoring.
SEARCH_ALIASES: dict[str, str] = {
    "k线": "stock_kline",
    "日k": "stock_kline",
    "均线": "stock_kline",
    "成交额": "stock_kline",
    "成交量": "stock_kline",
    "量能": "stock_kline",
    "财务": "stock_financials",
    "利润": "stock_financials",
    "营收": "stock_financials",
    "公司资料": "stock_profile",
    "公司介绍": "stock_profile",
    "公司": "stock_profile",
    "介绍": "stock_profile",
    "主营业务": "stock_profile",
    "市值": "stock_profile",
    "财报": "stock_financials",
    "龙虎榜": "dragon_tiger_list",
    "游资": "dragon_tiger_list",
    "新闻": "stock_news",
    "快讯": "finance_news",
    "首板评分": "first_board_ratings",
    "评级": "first_board_ratings",
    "复盘": "market_event_pool",
    "成分股": "index_constituents",
    "板块": "sector_performance",
}


class SearchToolsRequest(BaseModel):
    """Arguments for the model-facing tool search meta-tool."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(
        min_length=1,
        max_length=200,
        description="Capability, tool name or Chinese/English keywords, e.g. 成交额、财务、龙虎榜。",
    )
    limit: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Maximum number of matching tools to return.",
    )


def _parameters() -> dict[str, Any]:
    parameters = SearchToolsRequest.model_json_schema()
    parameters["additionalProperties"] = False
    return parameters


SEARCH_TOOLS_DEFINITION = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOLS_TOOL,
        "description": (
            "搜索当前可用的研究工具。当前工具列表可能不完整；缺少所需能力时，"
            "先用本工具按名称、能力或中英文关键词搜索。匹配工具会在下一轮自动加入。"
            "本工具不返回市场数据；不要猜测未列出的工具名，也不要用它替代查询。"
        ),
        "parameters": _parameters(),
    },
}


def resolve_progressive_tools_mode(value: str | None = None) -> str:
    """Resolve off/hybrid/full; invalid settings fail explicitly."""

    raw = (value if value is not None else os.getenv(PROGRESSIVE_TOOLS_ENV, "off"))
    normalized = str(raw).strip().lower()
    aliases = {
        "0": "off",
        "false": "off",
        "no": "off",
        "off": "off",
        "1": "hybrid",
        "true": "hybrid",
        "hybrid": "hybrid",
        "full": "full",
    }
    mode = aliases.get(normalized)
    if mode not in MODES:
        raise ValueError(
            f"{PROGRESSIVE_TOOLS_ENV} must be one of off, hybrid or full"
        )
    return mode


def resolve_max_active_tools(value: str | None = None) -> int:
    """Bound the active business set so discovery cannot rebuild the full catalog."""

    raw = value if value is not None else os.getenv(MAX_ACTIVE_TOOLS_ENV, "")
    if not str(raw).strip():
        return DEFAULT_MAX_ACTIVE_TOOLS
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        raise ValueError(
            f"{MAX_ACTIVE_TOOLS_ENV} must be an integer between 1 and {_MAX_ACTIVE_TOOLS}"
        ) from None
    if not 1 <= parsed <= _MAX_ACTIVE_TOOLS:
        raise ValueError(
            f"{MAX_ACTIVE_TOOLS_ENV} must be an integer between 1 and {_MAX_ACTIVE_TOOLS}"
        )
    return parsed


def _short_description(value: Any) -> str:
    return " ".join(str(value or "").split())[:180]


def _score(query: str, name: str, description: str, notes: str) -> int:
    folded_name = name.casefold()
    haystack = f"{name} {description} {notes}".casefold()
    score = 0
    if query == folded_name:
        score += 100
    if query in folded_name:
        score += 40
    if query in haystack:
        score += 20
    for term in _QUERY_SPLIT.split(query):
        if not term:
            continue
        if term in folded_name:
            score += 12
        if term in haystack:
            score += 6
    for alias, target in SEARCH_ALIASES.items():
        if alias in query and target == name:
            # Capability aliases are stronger evidence than a generic mention
            # in another tool's long description.
            score += 60
    # Chinese bigrams catch partial capability names without a tokenizer.
    for index in range(len(query) - 1):
        bigram = query[index:index + 2]
        if _CJK.match(bigram[0]) and bigram in haystack:
            score += 2
    return score


def search_tool_catalog(
    catalog: dict[str, dict[str, Any]],
    query: str,
    *,
    limit: int = 4,
    active_names: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return matching tools, marking ones already visible to the model."""

    normalized = " ".join(str(query or "").casefold().split())
    if not normalized:
        return []
    active = set(active_names)
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for name, info in catalog.items():
        description = str(info.get("description") or "")
        notes = str(info.get("notes") or "")
        score = _score(normalized, name, description, notes)
        if score > 0:
            scored.append((score, name, info))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "name": name,
            "description": _short_description(info.get("description")),
            "time_mode": info.get("time_mode"),
            "already_visible": name in active,
        }
        for _, name, info in scored[: max(1, min(limit, 8))]
    ]
