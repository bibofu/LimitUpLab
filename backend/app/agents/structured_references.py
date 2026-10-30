"""Committed, evidence-backed list references; never infer ranking from prose."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from app.agents.query_contract import MARKET_SEGMENT_PREFIXES
from app.agents.reference_intent import validated_reference_intent

_DATE_ARGUMENTS = {"trade_date", "start_date", "end_date", "requested_as_of", "as_of"}
_SORT_ARGUMENTS = {"sort_by", "sort_order"}
_A_SHARE_PREFIXES = tuple(prefix for prefixes in MARKET_SEGMENT_PREFIXES.values() for prefix in prefixes)
REFERENT_SCHEMA_VERSION = 1
# A single-stock query's symbol is the subject, not a filter for a new list.
SINGLE_STOCK_TOOLS = frozenset({
    "stock_kline", "stock_activity", "stock_news", "stock_financials",
})
# Tools whose rows are statistical summaries rather than stock lists.
ANALYSIS_TOOLS = frozenset({
    "daily_board_promotion", "market_summary", "sector_performance",
    "sector_stock_ranking", "hot_stock_ranking", "dragon_tiger_list",
    "recent_limit_up_returns", "post_limit_statistics", "market_event_pool",
    "stock_kline", "market_index_trend",
})


def _query_scope(value: Any, tool: str | None) -> dict[str, Any] | None:
    """Only a versioned, invocation-bound scope can prove query conditions."""
    if (not isinstance(value, dict) or value.get("scope_version") != 1
            or not tool or value.get("tool") != tool
            or not isinstance(value.get("arguments"), dict)
            or not isinstance(value.get("explicit_arguments"), dict)):
        return None
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return deepcopy(value)


def _scope_identity(scope: dict) -> str:
    return json.dumps([scope["tool"], scope["arguments"]], sort_keys=True, ensure_ascii=False)


def constraints_from_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve committed query parameters independently of prose summaries.

    These are conditions for continuing the referenced query, not inferred
    user preferences. Current user instructions remain authoritative.
    """
    constraints = []
    for reference in references:
        if reference.get("committed_status") != "committed" or not reference.get("source_run_id"):
            continue
        if reference.get("source_tool") in SINGLE_STOCK_TOOLS:
            continue
        scope = _query_scope(reference.get("query_scope"), reference.get("source_tool"))
        if scope is None:
            continue
        for key, value in scope["arguments"].items():
            if value is None:
                continue
            constraints.append({"scope": "session", "key": key, "value": deepcopy(value),
                "tool": scope["tool"], "source_run_id": reference["source_run_id"],
                "source_message_id": "user_" + reference["source_run_id"],
                "reference_id": reference["reference_id"], "origin": "committed_tool_scope",
                "application": "reference_followup"})
    return constraints


def supported_session_constraints(constraints: Any, references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain only typed conditions still exactly supported by active references."""
    candidates = constraints_from_references(references)
    return [deepcopy(item) for item in constraints if isinstance(item, dict) and item in candidates] \
        if isinstance(constraints, list) else []


def _symbol(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if len(text) == 6 and text.isascii() and text.isdigit() else None


def _mentions(line: str, symbol: str, name: str) -> bool:
    if name and name != symbol and name in line:
        return True
    start = 0
    while (position := line.find(symbol, start)) >= 0:
        before = line[position - 1] if position else ""
        after = line[position + 6] if position + 6 < len(line) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = position + 6
    return False


def _list_line(line: str) -> bool:
    if "|" in line or line.startswith(("- ", "* ")):
        return True
    prefix = line.split(" ", 1)[0]
    for separator in (".", "、", ")", "）"):
        head, found, _ = prefix.partition(separator)
        if found and head.isdigit():
            return True
    return False


def _displayed_order(answer: str, identities: dict[str, str]) -> list[str] | None:
    """Validate one visible list/table against structured source identities."""
    blocks: list[list[str]] = []
    block: list[str] = []
    for line in [*answer.splitlines(), ""]:
        line = line.strip()
        if not _list_line(line):
            if block:
                blocks.append(block)
                block = []
            continue
        block.append(line)
    table_candidates: list[list[str]] = []
    prose_candidates: list[list[str]] = []
    for lines in blocks:
        matches = [[symbol for symbol, name in identities.items() if _mentions(line, symbol, name)]
                   for line in lines]
        if not any(matches):
            continue
        order = []
        invalid = False
        for index, (line, matched) in enumerate(zip(lines, matches)):
            separator = "|" in line and bool(line.strip("| :-")) is False
            header = (index + 1 < len(lines) and "|" in lines[index + 1]
                      and not lines[index + 1].strip("| :-"))
            if separator or header:
                continue
            # An unrecognized middle row cannot be silently skipped: doing so
            # would change what "the second stock" means in the displayed list.
            if len(matched) != 1:
                invalid = True
                break
            order.extend(matched)
        if invalid or not order:
            continue
        # A Markdown table is an explicit displayed order. Incidental prose
        # bullets that mention the same names must not create a second
        # candidate and disable ordinal resolution.
        if any("|" in line for line in lines):
            table_candidates.append(order)
        else:
            prose_candidates.append(order)
    candidates = table_candidates or prose_candidates
    if len(candidates) != 1:
        return None
    order = candidates[0]
    return order if len(order) == len(set(order)) else None


def _display_block_order(
    response: dict[str, Any], offset: int, identities: dict[str, str],
) -> list[str] | None:
    """Use the deterministic display projection before parsing answer prose."""

    for block in response.get("display_blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "table":
            continue
        if block.get("source_tool_index") != offset:
            continue
        symbols = block.get("ordered_symbols")
        if not isinstance(symbols, list) or not symbols:
            return None
        if any(symbol not in identities for symbol in symbols):
            return None
        return symbols
    return None


def references_from_committed_result(run_id: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    """Call only AFTER delivery commit; cancelled/failed results never become references.

    Source order is retained for provenance, but ordinal resolution is enabled only
    when a single displayed list can be mapped uniquely to source symbols.
    """
    if not run_id or response.get("task_status") not in {"complete", "partial", "empty"}:
        return []
    if response.get("run_id") not in {None, run_id}:
        raise ValueError("Reference run does not match the committed response")
    references = []
    for offset, trace in enumerate(response.get("tool_results", [])):
        if (not isinstance(trace, dict) or trace.get("status") == "error"
                or trace.get("name") == "read_tool_result"):
            continue
        payload = trace.get("output")
        if not isinstance(payload, dict) or payload.get("status") == "error":
            continue
        rows = payload.get("rows")
        if not isinstance(rows, list):
            continue
        identities: dict[str, str] = {}
        duplicate = False
        for row in rows:
            if not isinstance(row, dict) or not (symbol := _symbol(row.get("symbol"))):
                continue
            duplicate = duplicate or symbol in identities
            identities[symbol] = str(row.get("name") or "")
        if not identities:
            continue
        block_order = _display_block_order(response, offset, identities)
        displayed = block_order if block_order else (
            None if duplicate else _displayed_order(str(response.get("answer") or ""), identities)
        )
        scope = _query_scope(payload.get("query_scope"), trace.get("name"))
        arguments = scope["arguments"] if scope is not None else {}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        references.append({
            "reference_id": f"{run_id}:{offset}:{digest[:12]}",
            "source_run_id": run_id,
            "reference_type": "display_block" if block_order else (
                "displayed_list" if displayed else "tool_result"
            ),
            "source_tool": trace.get("name"),
            "trade_date": payload.get("trade_date") or payload.get("data_as_of"),
            "ordered_symbols": displayed or list(identities),
            "source_symbols": list(identities),
            "query_scope": scope,
            "query_scope_status": "known" if scope is not None else "unknown",
            "filters": {key: deepcopy(value) for key, value in arguments.items()
                        if key not in _DATE_ARGUMENTS | _SORT_ARGUMENTS} if scope is not None else None,
            "sort_rules": {key: arguments[key] for key in sorted(_SORT_ARGUMENTS) if key in arguments}
                          if scope is not None else None,
            "result_hash": digest,
            "committed_status": "committed",
            "ordinal_resolvable": displayed is not None,
        })
    return references


def _a_share_symbol(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if len(text) == 6 and text.isascii() and text.isdigit() \
        and text.startswith(_A_SHARE_PREFIXES) else None


def _date_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text


def _payload_stock_name(payload: dict[str, Any], tool: str | None, row: dict[str, Any] | None = None) -> str:
    candidates = []
    if isinstance(row, dict):
        candidates.append(row.get("name"))
    candidates.extend((payload.get("stock_name"), payload.get("resolved_name")))
    for candidate in candidates:
        name = str(candidate or "").strip()
        if 1 < len(name) <= 20 and name != (tool or "") and name != payload.get("symbol"):
            return name
    summary = str(payload.get("summary") or "")
    matched = re.match(r"^\s*([^\s(（]{2,20})\s*[（(]\s*(\d{6})\s*[）)]", summary)
    if matched and matched.group(2) == str(payload.get("symbol") or ""):
        return matched.group(1)
    return ""


def _primary_stocks(tool: str | None, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Extract the primary stocks of one tool result, not incidental news mentions."""
    stocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    nested = False

    def add(symbol: Any, name: Any, trade_date: Any) -> None:
        valid = _a_share_symbol(symbol)
        if not valid or valid in seen:
            return
        seen.add(valid)
        stocks.append({"symbol": valid, "name": str(name or "").strip(),
                       "trade_date": _date_text(trade_date), "source_tool": tool,
                       "verified": True})

    top_symbol = _a_share_symbol(payload.get("symbol"))
    rows = payload.get("rows")
    row_symbols = 0
    if top_symbol:
        add(top_symbol, _payload_stock_name(payload, tool), payload.get("data_as_of")
            or payload.get("trade_date") or payload.get("end_date"))
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _a_share_symbol(row.get("symbol"))
            if symbol:
                row_symbols += 1
                add(symbol, row.get("name") or _payload_stock_name(payload, tool, row),
                    row.get("trade_date") or row.get("data_as_of")
                    or payload.get("trade_date") or payload.get("data_as_of")
                    or payload.get("end_date"))
            promoted = row.get("promoted_stocks")
            if isinstance(promoted, list):
                nested = True
                for item in promoted:
                    if isinstance(item, dict):
                        add(item.get("symbol"), item.get("name"),
                            item.get("trade_date") or row.get("trade_date")
                            or payload.get("trade_date") or payload.get("end_date"))
    group_kind = "list" if nested or row_symbols > 1 else "single"
    return stocks, group_kind


def _analysis_from_payload(tool: str | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    if tool not in ANALYSIS_TOOLS:
        return None
    scope = _query_scope(payload.get("query_scope"), tool)
    if scope is None:
        return None
    rows = payload.get("rows")
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    excluded = {"promoted_stocks", "buckets", "data_missing", "first_board_opening_missing_symbols"}
    metric_keys = [key for key, value in row.items()
                   if key not in excluded and isinstance(value, (int, float, bool))
                   and not key.endswith("_date")][:12]
    return {
        "tool": tool,
        "query_scope": scope,
        "metric_keys": metric_keys,
        "as_of": _date_text(row.get("trade_date") or payload.get("end_date")
                            or payload.get("data_as_of") or payload.get("as_of")),
        "previous_trade_date": _date_text(row.get("previous_trade_date")
                                          or payload.get("previous_trade_date")),
        "result_digest": hashlib.sha256(json.dumps(
            row, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
    }


def _date_scope_from_payload(tool: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    if tool == "trading_calendar":
        return {
            "as_of": _date_text(row.get("latest_closed_trade_date") or row.get("today")
                                or row.get("as_of")),
            "previous_trade_date": _date_text(row.get("previous_trade_date")),
            "next_trade_date": _date_text(row.get("next_trade_date")),
        }
    return {
        "as_of": _date_text(row.get("trade_date") or payload.get("end_date")
                            or payload.get("data_as_of")),
        "previous_trade_date": _date_text(row.get("previous_trade_date")
                                          or payload.get("previous_trade_date")),
        "next_trade_date": None,
    }


def _merge_date_scope(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if value and not merged.get(key):
            merged[key] = value
    return merged


def build_conversation_referents(run_id: str | None, response: dict[str, Any]) -> dict[str, Any]:
    """Build bounded, source-bound referents for one committed answer.

    Referents answer "what is the user talking about", never "what are the
    current facts". They are built at run level so multiple single-stock tool
    calls can form one coherent entity set.
    """
    if not run_id or response.get("task_status") not in {"complete", "partial", "empty"}:
        return {}
    if response.get("run_id") not in {None, run_id}:
        raise ValueError("Referent run does not match the committed response")

    groups: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    date_scope: dict[str, Any] = {}
    for trace in response.get("tool_results", []):
        if not isinstance(trace, dict) or trace.get("status") == "error" \
                or trace.get("name") == "read_tool_result":
            continue
        tool = trace.get("name")
        payload = trace.get("output")
        if not isinstance(payload, dict) or payload.get("status") == "error":
            continue
        stocks, group_kind = _primary_stocks(tool, payload)
        if stocks:
            groups.append({"kind": group_kind, "tool": tool, "stocks": stocks})
        analysis = _analysis_from_payload(tool, payload)
        if analysis is not None:
            analyses.append(analysis)
        date_scope = _merge_date_scope(date_scope, _date_scope_from_payload(tool, payload))

    list_groups = [group for group in groups if group["kind"] == "list"]
    single_groups = [group for group in groups if group["kind"] == "single"]
    multiple = bool(list_groups and single_groups) or len(list_groups) > 1
    if not multiple and len(list_groups) == 1:
        selected = list_groups[0]["stocks"]
        kind = "list"
    elif not multiple and single_groups:
        selected = [stock for group in single_groups for stock in group["stocks"]]
        kind = "set"
    elif not groups:
        selected = []
        kind = "none"
    else:
        selected = [stock for group in groups for stock in group["stocks"]]
        kind = "multiple"

    deduped: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for stock in selected:
        if stock["symbol"] in seen_symbols:
            continue
        seen_symbols.add(stock["symbol"])
        deduped.append(deepcopy(stock))
    kind = "single" if len(deduped) == 1 and kind != "multiple" else kind

    displayed = None
    if deduped and kind != "multiple":
        identities = {stock["symbol"]: stock["name"] for stock in deduped}
        displayed = _displayed_order(str(response.get("answer") or ""), identities)
    ordered_symbols = displayed or []
    ordinal_resolvable = displayed is not None
    if kind == "single":
        ordered_symbols = [deduped[0]["symbol"]]
        ordinal_resolvable = True
    if kind == "set" and displayed is not None:
        kind = "ordered_list"

    return {
        "referent_schema_version": REFERENT_SCHEMA_VERSION,
        "source_run_id": run_id,
        "source_message_id": "answer_" + run_id,
        "stocks": deduped,
        "stock_set": {
            "kind": kind,
            "display_order_valid": displayed is not None,
            "ordinal_resolvable": ordinal_resolvable,
            "ordered_symbols": ordered_symbols,
            "groups": [{"tool": group["tool"], "kind": group["kind"],
                        "symbols": [stock["symbol"] for stock in group["stocks"]]}
                       for group in groups],
        },
        "analysis": analyses[0] if len(analyses) == 1 else None,
        "analyses": analyses,
        "date_scope": date_scope,
    }


def history_stock_hint(recent_messages: list[Any]) -> dict[str, Any] | None:
    """Use only the immediately previous assistant turn as an unverified pointer."""
    assistant = None
    for item in reversed(recent_messages or []):
        role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
        if role == "assistant":
            assistant = item
            break
    if assistant is None:
        return None
    metadata = assistant.get("metadata") if isinstance(assistant, dict) else {}
    mentions = metadata.get("stock_mentions") if isinstance(metadata, dict) else None
    if isinstance(mentions, list) and mentions:
        unique: dict[str, str] = {}
        for mention in mentions:
            if isinstance(mention, dict):
                symbol = _a_share_symbol(mention.get("symbol"))
                if symbol:
                    unique[symbol] = str(mention.get("name") or "").strip()
        if len(unique) == 1:
            symbol, name = next(iter(unique.items()))
            return {"symbol": symbol, "name": name, "source": "assistant_history",
                    "verified": False}
        if len(unique) > 1:
            return None
    content = assistant.get("content") if isinstance(assistant, dict) else str(assistant)
    if not isinstance(content, str) or not content.strip():
        return None
    pairs = [(name, symbol) for name, symbol in
             re.findall(r"([一-鿿A-Za-z0-9\-]{2,20})\s*[（(]\s*(\d{6})\s*[）)]", content)
             if _a_share_symbol(symbol)]
    if len({symbol for _, symbol in pairs}) == 1:
        name, symbol = pairs[0]
        return {"symbol": symbol, "name": name, "source": "assistant_history",
                "verified": False}
    codes = sorted({code for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", content)
                    if _a_share_symbol(code)})
    if len(codes) == 1:
        return {"symbol": codes[0], "name": "", "source": "assistant_history",
                "verified": False}
    return None


def compact_referent_hint(conversation: dict[str, Any] | None) -> dict[str, Any] | None:
    """Small, bounded hint for input review; never the full conversation."""
    conversation = conversation or {}
    referents = conversation.get("referents")
    hint: dict[str, Any] = {}
    if isinstance(referents, dict):
        stocks = referents.get("stocks")
        stock_set = referents.get("stock_set")
        if isinstance(stocks, list) and stocks:
            ordered = stock_set.get("ordered_symbols") if isinstance(stock_set, dict) else None
            if isinstance(ordered, list) and ordered:
                by_symbol = {item.get("symbol"): item for item in stocks if isinstance(item, dict)}
                selected = [by_symbol[symbol] for symbol in ordered if symbol in by_symbol]
            else:
                selected = [item for item in stocks if isinstance(item, dict)]
            hint["stocks"] = [{"symbol": item.get("symbol"), "name": item.get("name")}
                              for item in selected[:10]]
            if isinstance(stock_set, dict):
                hint["stock_set_kind"] = stock_set.get("kind")
        analyses = referents.get("analyses")
        if isinstance(analyses, list) and analyses:
            analysis = analyses[0] if len(analyses) == 1 and isinstance(analyses[0], dict) else None
            if analysis:
                hint["analysis"] = {key: analysis.get(key) for key in (
                    "tool", "metric_keys", "as_of", "previous_trade_date")}
                hint["analysis_count"] = 1
            else:
                hint["analysis_count"] = len(analyses)
        date_scope = referents.get("date_scope")
        if isinstance(date_scope, dict) and date_scope:
            hint["date_scope"] = {key: date_scope.get(key) for key in (
                "as_of", "previous_trade_date", "next_trade_date")}
    if not hint.get("stocks"):
        history = history_stock_hint(conversation.get("recent_messages") or [])
        if history:
            hint["history_stock"] = history
    return hint or None


def _page_conflict(symbols: list[str], page_context: dict | None) -> bool:
    page_symbol = _symbol((page_context or {}).get("symbol"))
    if not page_symbol:
        return False
    return page_symbol not in symbols if len(symbols) > 1 else symbols != [page_symbol]


def _compact_text(value: Any) -> str:
    return "".join(str(value or "").split())


def _named_entity(quote: Any, stocks: list[dict[str, Any]]) -> str | None:
    """Resolve one explicitly named or coded stock from the prior entity set.

    A message that spells out a stock name is not an ambiguous "which one" set
    reference, even when the same stock appeared in the previous list.
    """

    text = _compact_text(quote)
    if not text:
        return None
    exact: list[str] = []
    contained: list[str] = []
    for stock in stocks:
        symbol = _symbol(stock.get("symbol"))
        if not symbol:
            continue
        name = _compact_text(stock.get("name"))
        if text == symbol or (name and text == name):
            exact.append(symbol)
        elif symbol in text:
            contained.append(symbol)
        elif name and len(name) >= 4 and name in text:
            contained.append(symbol)
    exact = list(dict.fromkeys(exact))
    if len(exact) == 1:
        return exact[0]
    contained = list(dict.fromkeys(contained))
    if not exact and len(contained) == 1:
        return contained[0]
    return None


def _resolve_entities_from_referents(reviewed, referents: dict[str, Any],
                                     page_context: dict | None) -> dict[str, Any]:
    stocks = [item for item in referents.get("stocks") or [] if isinstance(item, dict)
              and _symbol(item.get("symbol"))]
    if not stocks:
        return {"status": "none"}
    stock_set = referents.get("stock_set") or {}
    set_kind = str(stock_set.get("kind") or "set")
    if set_kind == "multiple":
        return {"status": "clarify",
                "clarification": "上一轮包含多组名单或未能确认展示顺序，请明确股票代码或名单。"}
    ordered = [symbol for symbol in stock_set.get("ordered_symbols") or [] if _symbol(symbol)]
    symbols = ordered or list(dict.fromkeys(_symbol(item["symbol"]) for item in stocks))
    ordinal = reviewed.ordinal
    if reviewed.all_entities:
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return {"status": "clarify", "clarification": "上一轮没有可解析的股票实体。"}
        if _page_conflict(symbols, page_context):
            return {"status": "clarify", "clarification": "当前页面股票与所指名单不一致，请明确要查询哪一只。"}
        return {"status": "resolved", "kind": "entities", "symbols": symbols,
                "stock_set_kind": set_kind, "verified": True, "source": "tool",
                "source_run_id": referents.get("source_run_id"), "trade_date": None,
                "filters": None, "sort_rules": None, "query_scope": None,
                "query_scope_status": "unknown"}
    if ordinal is not None:
        if not stock_set.get("ordinal_resolvable") or not ordered or not 1 <= ordinal <= len(ordered):
            return {"status": "clarify", "clarification": "上一轮未能确认展示顺序，请明确股票代码。"}
        selected = [ordered[ordinal - 1]]
    elif (named := _named_entity(reviewed.quote, stocks)) is not None:
        selected = [named]
    elif len(symbols) == 1:
        selected = symbols
    else:
        return {"status": "clarify",
                "clarification": "请明确所指股票的代码或在上一份名单中的序号。"}
    if _page_conflict(selected, page_context):
        return {"status": "clarify", "clarification": "当前页面股票与所指名单不一致，请明确要查询哪一只。"}
    return {"status": "resolved", "kind": "entities", "symbols": selected,
            "stock_set_kind": set_kind, "verified": True, "source": "tool",
            "source_run_id": referents.get("source_run_id"), "trade_date": None,
            "filters": None, "sort_rules": None, "query_scope": None,
            "query_scope_status": "unknown"}


def _analysis_date(analysis: dict[str, Any]) -> str | None:
    """Read a date only from an analysis' own scope, never from another tool."""
    scope = analysis.get("query_scope")
    arguments = scope.get("arguments") if isinstance(scope, dict) else {}
    if not isinstance(arguments, dict):
        arguments = {}
    return _date_text(
        analysis.get("as_of") or arguments.get("trade_date")
        or arguments.get("requested_as_of") or arguments.get("as_of")
        or arguments.get("end_date")
    )


def _resolve_analysis(reviewed, referents: dict[str, Any]) -> dict[str, Any] | None:
    analyses = [item for item in referents.get("analyses") or [] if isinstance(item, dict)]
    if not analyses:
        return None
    if len(analyses) != 1:
        # Multiple tools can describe the same date. The date is still an
        # unambiguous reference; only the metric/tool remains unresolved.
        dates = {date for analysis in analyses if (date := _analysis_date(analysis))}
        if len(dates) != 1:
            return {"status": "clarify",
                    "clarification": "上一轮包含多个分析范围，请明确要继续哪个指标或日期。"}
        common_date = next(iter(dates))
        date_scope = referents.get("date_scope") or {}
        if reviewed.date_override == "previous_trade_date":
            resolved_date = date_scope.get("previous_trade_date") or next(
                (analysis.get("previous_trade_date") for analysis in analyses
                 if analysis.get("previous_trade_date")), None)
        elif reviewed.date_override == "today":
            resolved_date = date_scope.get("as_of") or common_date
        else:
            resolved_date = common_date
        return {
            "status": "resolved", "kind": "analysis", "symbols": [],
            "analysis_scope": None,
            "analysis_scopes": [analysis.get("query_scope") for analysis in analyses],
            "tool": None, "metric_keys": [],
            "as_of": common_date, "date_override": reviewed.date_override,
            "resolved_date": resolved_date, "verified": True,
            "source": "tool", "source_run_id": referents.get("source_run_id"),
            "trade_date": None, "filters": None, "sort_rules": None,
            "query_scope": None, "query_scope_status": "multiple",
        }
    analysis = analyses[0]
    date_scope = referents.get("date_scope") or {}
    resolved_date = None
    if reviewed.date_override == "previous_trade_date":
        resolved_date = analysis.get("previous_trade_date") or date_scope.get("previous_trade_date")
    elif reviewed.date_override == "today":
        resolved_date = date_scope.get("as_of") or analysis.get("as_of")
    return {"status": "resolved", "kind": "analysis", "symbols": [],
            "analysis_scope": analysis.get("query_scope"), "tool": analysis.get("tool"),
            "metric_keys": analysis.get("metric_keys") or [],
            "as_of": analysis.get("as_of"), "date_override": reviewed.date_override,
            "resolved_date": resolved_date, "verified": True, "source": "tool",
            "source_run_id": referents.get("source_run_id"), "trade_date": None,
            "filters": None, "sort_rules": None, "query_scope": analysis.get("query_scope"),
            "query_scope_status": "known" if analysis.get("query_scope") else "unknown"}


def _resolve_conditions_from_referents(reviewed, referents: dict[str, Any]) -> dict[str, Any] | None:
    analyses = [item for item in referents.get("analyses") or [] if isinstance(item, dict)]
    if not analyses:
        return None
    scopes = [analysis.get("query_scope") for analysis in analyses]
    if any(scope is None for scope in scopes):
        return {"status": "clarify",
                "clarification": "上一轮未保留可核验的查询条件，请重新说明指标或日期。"}
    if len({_scope_identity(scope) for scope in scopes}) != 1:
        return {"status": "clarify",
                "clarification": "上一轮查询使用了不同条件，请明确要继续哪组条件。"}
    scope = scopes[-1]
    arguments = scope["arguments"]
    return {"status": "resolved", "kind": "conditions", "symbols": [],
            "query_scope": scope, "query_scope_status": "known",
            "filters": {key: deepcopy(value) for key, value in arguments.items()
                        if key not in _DATE_ARGUMENTS | _SORT_ARGUMENTS},
            "sort_rules": {key: arguments[key] for key in sorted(_SORT_ARGUMENTS) if key in arguments},
            "verified": True, "source": "tool",
            "source_run_id": referents.get("source_run_id")}


def _resolve_legacy_reference(reviewed, references: list[dict[str, Any]],
                              page_context: dict | None) -> dict[str, Any]:
    ordinal = reviewed.ordinal
    if reviewed.kind == "entities" and ordinal is None and not reviewed.all_entities:
        return {"status": "clarify", "clarification": "请明确所指股票的代码或在上一份名单中的序号。"}
    valid = [item for item in references if item.get("committed_status") == "committed"]
    if not valid:
        if reviewed.kind == "conditions":
            return {"status": "clarify",
                    "clarification": "上一轮未保留可核验的查询条件，请重新说明指标或日期。"}
        return {"status": "clarify", "clarification": "尚无已提交的股票列表，请明确股票或先查询名单。"}
    latest_run = valid[-1].get("source_run_id")
    latest = [item for item in valid if item.get("source_run_id") == latest_run]
    if reviewed.kind == "conditions":
        scopes = [_query_scope(item.get("query_scope"), item.get("source_tool")) for item in latest]
        if any(scope is None for scope in scopes):
            return {"status": "clarify", "clarification": "上一份结果未保留可核验的查询条件，请重新说明筛选条件。"}
        if len({_scope_identity(scope) for scope in scopes}) != 1:
            return {"status": "clarify", "clarification": "上一轮查询使用了不同条件，请明确要继续哪组条件。"}
        # Conditions derive only from actual invocation parameters. Output rows
        # are never promoted into an implicit symbol filter for a new screening.
        selected = latest[-1]
        return {"status": "resolved", "kind": "conditions", "symbols": [],
                "reference_id": selected["reference_id"], "query_scope": scopes[-1],
                "query_scope_status": "known", "filters": selected.get("filters"),
                "sort_rules": selected.get("sort_rules"), "source_run_id": latest_run}
    distinct = {(tuple(item.get("ordered_symbols", [])), item.get("trade_date")) for item in latest}
    if len(distinct) != 1 or (ordinal is not None and not all(item.get("ordinal_resolvable") for item in latest)):
        return {"status": "clarify", "clarification": "上一轮包含多组名单或未能确认展示顺序，请明确股票代码或名单。"}
    selected = latest[-1]
    symbols = selected.get("ordered_symbols", [])
    if ordinal is not None:
        if not 1 <= ordinal <= len(symbols):
            return {"status": "clarify", "clarification": f"上一份名单共 {len(symbols)} 只，请明确要查询的股票。"}
        symbols = [symbols[ordinal - 1]]
    page_symbol = _symbol((page_context or {}).get("symbol"))
    if page_symbol and symbols != [page_symbol]:
        return {"status": "clarify", "clarification": "当前页面股票与所指名单不一致，请明确要查询哪一只。"}
    scope = _query_scope(selected.get("query_scope"), selected.get("source_tool"))
    return {"status": "resolved", "kind": "entities", "reference_id": selected["reference_id"],
            "symbols": list(symbols), "trade_date": selected.get("trade_date"),
            "filters": selected.get("filters") if scope is not None else None,
            "sort_rules": selected.get("sort_rules") if scope is not None else None,
            "query_scope": scope, "query_scope_status": "known" if scope is not None else "unknown",
            "source_run_id": latest_run}


def resolve_reference(query: str, references: list[dict[str, Any]], page_context: dict | None = None,
                      *, intent=None, referents: dict[str, Any] | None = None,
                      recent_messages: list[Any] | None = None) -> dict[str, Any]:
    """Resolve only a reviewed historical reference, never arbitrary new-query ranks.

    Referents are pointers, not evidence. A history-derived pointer is marked
    ``verified=False`` and must be verified by a current tool before answering.
    """
    reviewed = validated_reference_intent(intent or {"kind": "none"}, query)
    if reviewed.kind == "none":
        return {"status": "none"}
    if isinstance(referents, dict) and referents.get("referent_schema_version") in {
            None, REFERENT_SCHEMA_VERSION}:
        if reviewed.kind == "analysis":
            resolved = _resolve_analysis(reviewed, referents)
            if resolved is not None:
                return resolved
            return {"status": "clarify",
                    "clarification": "上一轮未保留可核验的分析范围，请重新说明指标或日期。"}
        if reviewed.kind == "conditions":
            resolved = _resolve_conditions_from_referents(reviewed, referents)
            if resolved is not None:
                return resolved
        if reviewed.kind == "entities":
            resolved = _resolve_entities_from_referents(reviewed, referents, page_context)
            if resolved["status"] != "none":
                return resolved
    if reviewed.kind == "analysis":
        return {"status": "clarify",
                "clarification": "上一轮未保留可核验的分析范围，请重新说明指标或日期。"}
    if reviewed.kind == "entities":
        hint = history_stock_hint(recent_messages or [])
        if hint and reviewed.ordinal is None and not reviewed.all_entities:
            return {"status": "resolved", "kind": "entities", "symbols": [hint["symbol"]],
                    "stock_set_kind": "single", "verified": False, "source": "assistant_history",
                    "requires_tool_verification": True, "source_run_id": None,
                    "trade_date": None, "filters": None, "sort_rules": None,
                    "query_scope": None, "query_scope_status": "unknown"}
    return _resolve_legacy_reference(reviewed, references, page_context)
