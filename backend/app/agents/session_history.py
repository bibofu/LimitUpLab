"""Deterministic session recap data for questions that ask what happened earlier."""

from __future__ import annotations

import re
from typing import Any

MAX_LEDGER_TURNS = 30
MAX_QUESTION_CHARS = 300
MAX_ANSWER_CHARS = 240
MAX_TOOLS = 8
MAX_STOCKS = 8
_RECAP_TERMS = (
    "回顾本会话", "本会话", "本次会话", "会话回顾", "前面问过",
    "前面查询过", "刚才的会话", "这轮会话", "整个会话",
)
_SYMBOL = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_CN_DATE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
_DETAILED_STOCK_TOOLS = frozenset({
    "stock_profile", "stock_kline", "stock_financials", "stock_news", "stock_activity",
})
_INDEX_NAMES = ("上证指数", "深证成指", "创业板指")
_FACT_QUERY_TERMS = (
    "最先", "最初", "最早", "第一次", "详细研究", "比较过", "指数",
    "龙虎榜", "日期",
)


def is_session_recap_query(query: str) -> bool:
    text = str(query or "")
    return any(term in text for term in _RECAP_TERMS)


def session_facts_cover_query(query: str) -> bool:
    """Whether the deterministic facts actually answer this recap question."""

    text = str(query or "")
    return any(term in text for term in _FACT_QUERY_TERMS)


def _value(item: Any, key: str, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _metadata(item: Any) -> dict:
    value = _value(item, "metadata", None)
    if not isinstance(value, dict):
        value = _value(item, "additional_kwargs", None)
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tool_names(meta: dict) -> list[str]:
    names = meta.get("tool_calls")
    if not isinstance(names, list) or not names:
        names = [
            item.get("name") for item in meta.get("tool_results") or []
            if isinstance(item, dict) and item.get("name")
        ]
    result = []
    for name in names or []:
        if isinstance(name, str) and name and name != "read_tool_result" and name not in result:
            result.append(name)
    return result[:MAX_TOOLS]


def _stocks(meta: dict, content: str) -> list[dict[str, str]]:
    values = []
    mentions = meta.get("stock_mentions")
    if isinstance(mentions, list):
        for item in mentions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip()
            if symbol and symbol not in {value["symbol"] for value in values}:
                values.append({"symbol": symbol, "name": _text(item.get("name"), 30)})
    if not values:
        for symbol in _SYMBOL.findall(content):
            if symbol not in {value["symbol"] for value in values}:
                values.append({"symbol": symbol, "name": ""})
    return values[:MAX_STOCKS]


def build_session_ledger(history: list[Any] | None) -> list[dict[str, Any]]:
    """Build a compact, committed-turn index for session recap questions.

    It is a pointer index, not market evidence. Detailed values still need a
    current-run tool query.
    """

    turns: list[dict[str, Any]] = []
    current = None
    for item in history or []:
        role = _value(item, "role")
        content = _value(item, "content", "")
        if role == "user":
            current = {
                "question": _text(content, MAX_QUESTION_CHARS),
                "answer_status": None,
                "stop_reason": None,
                "tools": [],
                "stocks": [],
                "answer_excerpt": "",
                "_question_stocks": _stocks({}, str(content or "")),
            }
            turns.append(current)
        elif role == "assistant" and current is not None:
            meta = _metadata(item)
            current["answer_status"] = meta.get("task_status") or _value(item, "status", "success")
            current["stop_reason"] = meta.get("stop_reason")
            current["tools"] = _tool_names(meta)
            current["stocks"] = _stocks(meta, str(content or "")) or current["_question_stocks"]
            current["answer_excerpt"] = _text(content, MAX_ANSWER_CHARS)
    for turn in turns:
        turn.pop("_question_stocks", None)
    return turns[-MAX_LEDGER_TURNS:]


def _date_from_text(text: str) -> str | None:
    matched = _DATE.search(text)
    if matched:
        return matched.group(1)
    matched = _CN_DATE.search(text)
    if matched:
        year, month, day = matched.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _named_stock(ledger: list[dict[str, Any]], stock: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(stock, dict):
        return None
    symbol = stock.get("symbol")
    if stock.get("name") or not symbol:
        return stock
    for turn in ledger:
        for candidate in turn.get("stocks") or []:
            if candidate.get("symbol") == symbol and candidate.get("name"):
                return candidate
    return stock


def build_session_facts(ledger: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Derive the few deterministic recap answers the model must not guess."""

    first_detailed = None
    first_date = None
    compared_indices: list[str] = []
    dragon_tiger_followup = None
    for turn in ledger or []:
        tools = set(turn.get("tools") or [])
        stocks = turn.get("stocks") or []
        question = str(turn.get("question") or "")
        if first_detailed is None and tools.intersection(_DETAILED_STOCK_TOOLS) and stocks:
            first_detailed = stocks[0]
        if first_date is None:
            first_date = _date_from_text(question)
        if "market_index_trend" in tools:
            compared_indices = list(_INDEX_NAMES)
        if (not is_session_recap_query(question)
                and ("上榜" in question or "席位" in question)
                and len(stocks) == 1):
            dragon_tiger_followup = stocks[0]
    return {
        "first_detailed_stock": _named_stock(ledger or [], first_detailed),
        "first_explicit_date": first_date,
        "compared_indices": compared_indices,
        "dragon_tiger_followup_stock": _named_stock(ledger or [], dragon_tiger_followup),
    }


def format_session_recap(facts: dict[str, Any] | None) -> str:
    """Format deterministic recap facts; never include market values as evidence."""

    facts = facts or {}
    lines = ["**本会话回顾（来自已提交消息索引）**"]
    detailed = facts.get("first_detailed_stock")
    if isinstance(detailed, dict) and detailed.get("symbol"):
        name = detailed.get("name") or ""
        label = f"{name}（{detailed['symbol']}）" if name else detailed["symbol"]
        lines.append(f"- 最先详细研究的股票：{label}")
    if facts.get("first_explicit_date"):
        lines.append(f"- 最初采用的已收盘日期（首个明确日期）：{facts['first_explicit_date']}")
    indices = [str(item) for item in facts.get("compared_indices") or [] if item]
    if indices:
        lines.append(f"- 比较过的指数：{'、'.join(indices)}")
    dragon = facts.get("dragon_tiger_followup_stock")
    if isinstance(dragon, dict) and dragon.get("symbol"):
        name = dragon.get("name") or ""
        label = f"{name}（{dragon['symbol']}）" if name else dragon["symbol"]
        lines.append(f"- 龙虎榜追问的股票：{label}")
    lines.append("说明：以上是会话对象索引，不是本轮行情证据；具体数值需重新查询。")
    return "\n".join(lines)
