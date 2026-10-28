"""Deterministic guards for answers that require current-run tool evidence."""

from __future__ import annotations

from app.agents.session_history import is_session_recap_query


# Queries that clearly need a specific current-run tool. This is deliberately
# narrow: it catches data questions that previously reused stale/hallucinated
# context instead of querying the tool in this run.
_REQUIRED_TOOL_TERMS = (
    ("stock_kline", (
        "k线", "K线", "收盘价", "成交量", "均线", "量比", "回撤",
        "价格与成交量", "二十个交易日", "20个交易日",
    )),
    ("dragon_tiger_list", (
        "龙虎榜", "上榜原因", "上榜", "席位", "买入额", "卖出额",
        "净买额", "机构净买", "游资净买",
    )),
    ("market_index_trend", (
        "上证指数", "深证成指", "创业板指", "三大指数",
    )),
)
_DEFINITION_TERMS = ("什么是", "是什么意思", "解释一下", "怎么理解")
_SKIP_STATUSES = frozenset({"clarify", "refuse", "cancelled", "error"})


def required_tool_for_query(query: str) -> str | None:
    """Return the tool a data question needs in the current run, if unambiguous."""

    text = str(query or "").casefold()
    if not text or is_session_recap_query(text):
        return None
    if any(term in text for term in _DEFINITION_TERMS):
        return None
    for tool, terms in _REQUIRED_TOOL_TERMS:
        if any(term.casefold() in text for term in terms):
            return tool
    return None


def answered_with_data_values(answer: str | None) -> bool:
    """A prose answer with no digits is not making a market-data claim."""
    return any(character.isdigit() for character in str(answer or ""))


def missing_required_tool(query: str, tool_calls: list[str] | None, status: str | None,
                          *, answer: str | None = None) -> bool:
    """Whether a non-terminal answer lacks the tool required by its own question."""

    if status in _SKIP_STATUSES:
        return False
    if answer is not None and not answered_with_data_values(answer):
        return False
    required = required_tool_for_query(query)
    return bool(required and required not in set(tool_calls or []))
