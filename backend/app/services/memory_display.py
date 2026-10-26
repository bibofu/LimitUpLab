"""Deterministic tool-display projections from scoped memory preferences."""

from __future__ import annotations

import re
from typing import Any


COLUMN_ALIASES = {
    "股票名称": "name",
    "名称": "name",
    "股票代码": "symbol",
    "代码": "symbol",
    "板块": "industry",
    "行业": "industry",
    "概念": "concept",
    "题材": "concepts",
    "涨跌幅": "change_pct",
    "涨幅": "change_pct",
    "买入额": "buy_amount",
    "卖出额": "sell_amount",
    "净买额": "net_buy_amount",
    "净买入": "net_buy_amount",
    "净买率": "net_rate",
    "机构净买额": "organization_net_buy_amount",
    "游资净买额": "hot_money_net_buy_amount",
    "换手率": "turnover_rate",
    "成交额": "amount",
    "成交金额": "amount",
    "板数": "board_height",
    "评分": "score",
    "原因": "limit_reason",
    "上榜原因": "limit_reason",
}
COLUMN_LABELS = {
    "name": "股票名称",
    "symbol": "代码",
    "industry": "行业",
    "concept": "概念",
    "concepts": "题材",
    "change_pct": "涨跌幅",
    "buy_amount": "买入额",
    "sell_amount": "卖出额",
    "net_buy_amount": "净买额",
    "net_rate": "净买率",
    "organization_net_buy_amount": "机构净买额",
    "hot_money_net_buy_amount": "游资净买额",
    "turnover_rate": "换手率",
    "amount": "成交额",
    "board_height": "板数",
    "score": "评分",
    "limit_reason": "上榜原因",
}
QUERY_COLUMN_TERMS = tuple(COLUMN_ALIASES)


def _normalize_columns(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        column = COLUMN_ALIASES.get(text, text)
        if column not in result:
            result.append(column)
    return result


def _derive_columns(content: str) -> list[str]:
    """Fallback for simple tool-display preferences when structured fields are absent."""

    text = content.strip()
    if not text:
        return []
    requested = [term for term in QUERY_COLUMN_TERMS if term in text]
    columns = []
    for term in requested:
        column = COLUMN_ALIASES[term]
        if column not in columns:
            columns.append(column)
    # “只显示股票名称” should not also imply a second column from an incidental word.
    if "名称" in text and "代码" not in text and "name" not in columns:
        columns.append("name")
    return columns


def _query_requests_other_columns(query: str, columns: list[str]) -> bool:
    """Current explicit field requests override a long-term display preference."""

    for term, column in COLUMN_ALIASES.items():
        if term in query and column not in columns:
            return True
    return False


def display_policies_for(
    items: list[dict[str, Any]], query: str,
) -> list[dict[str, Any]]:
    """Extract at most one usable display policy per tool."""

    policies: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("scope_type") != "tool":
            continue
        scope = item.get("scope") or {}
        tool_name = str(scope.get("tool_name") or "").strip()
        if not tool_name:
            continue
        structured = item.get("structured") or {}
        columns = _normalize_columns(structured.get("display_columns")) if isinstance(structured, dict) else []
        if not columns:
            columns = _derive_columns(str(item.get("value") or ""))
        if not columns or _query_requests_other_columns(query, columns):
            continue
        policies.setdefault(tool_name, {
            "tool_name": tool_name,
            "columns": columns,
            "memory_id": item.get("memory_id"),
            "scope_key": item.get("scope_key"),
        })
    return list(policies.values())


def build_display_blocks(
    tool_messages: list[dict[str, Any]],
    policies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project complete tool rows into deterministic display tables."""

    by_tool = {policy["tool_name"]: policy for policy in policies}
    blocks = []
    for index, observation in enumerate(tool_messages):
        if not isinstance(observation, dict):
            continue
        tool_name = str(observation.get("name") or "")
        policy = by_tool.get(tool_name)
        rows = observation.get("rows")
        if policy is None or not isinstance(rows, list) or not rows:
            continue
        columns = list(policy["columns"])
        projected = []
        ordered_symbols = []
        present = set()
        symbols_complete = True
        for row in rows:
            if not isinstance(row, dict):
                continue
            projected.append({column: row.get(column) for column in columns})
            present.update(column for column in columns if column in row)
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                ordered_symbols.append(symbol)
            else:
                symbols_complete = False
        if not projected:
            continue
        row_count = observation.get("row_count")
        if not isinstance(row_count, int) or row_count < len(projected):
            row_count = len(projected)
        truncated = bool(
            observation.get("truncated")
            or observation.get("source_truncated")
            or len(projected) < row_count
        )
        if len(ordered_symbols) != len(projected) or len(set(ordered_symbols)) != len(ordered_symbols):
            symbols_complete = False
        blocks.append({
            "kind": "table",
            "source_tool": tool_name,
            "source_tool_index": index,
            "columns": columns,
            "column_labels": {column: COLUMN_LABELS.get(column, column) for column in columns},
            "rows": projected,
            "row_count": row_count,
            "displayed_count": len(projected),
            "ordered_symbols": ordered_symbols if symbols_complete else [],
            "missing_columns": [column for column in columns if column not in present],
            "truncated": truncated,
            "display_policy_id": policy.get("memory_id"),
        })
    return blocks
