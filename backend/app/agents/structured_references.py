"""Committed, evidence-backed list references; never infer ranking from prose."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

_DATE_ARGUMENTS = {"trade_date", "start_date", "end_date", "requested_as_of", "as_of"}
_SORT_ARGUMENTS = {"sort_by", "sort_order"}


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
    candidates: list[list[str]] = []
    for lines in blocks:
        matches = [[symbol for symbol, name in identities.items() if _mentions(line, symbol, name)]
                   for line in lines]
        if not any(matches):
            continue
        order = []
        for index, (line, matched) in enumerate(zip(lines, matches)):
            separator = "|" in line and bool(line.strip("| :-")) is False
            header = (index + 1 < len(lines) and "|" in lines[index + 1]
                      and not lines[index + 1].strip("| :-"))
            if separator or header:
                continue
            # An unrecognized middle row cannot be silently skipped: doing so
            # would change what "the second stock" means in the displayed list.
            if len(matched) != 1:
                return None
            order.extend(matched)
        candidates.append(order)
    if len(candidates) != 1:
        return None
    order = candidates[0]
    return order if len(order) == len(set(order)) else None


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
        displayed = None if duplicate else _displayed_order(str(response.get("answer") or ""), identities)
        scope = _query_scope(payload.get("query_scope"), trace.get("name"))
        arguments = scope["arguments"] if scope is not None else {}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        references.append({
            "reference_id": f"{run_id}:{offset}:{digest[:12]}",
            "source_run_id": run_id,
            "reference_type": "displayed_list" if displayed else "tool_result",
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


def _ordinal(query: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for start, character in enumerate(query):
        if character != "第":
            continue
        end = start + 1
        while end < len(query) and (query[end].isdigit() or query[end] in digits):
            end += 1
        if end == start + 1 or not query[end:].startswith(("只", "个", "名", "支")):
            continue
        raw = query[start + 1:end]
        if raw.isascii() and raw.isdigit():
            return int(raw)
        if raw in digits:
            return digits[raw]
        if "十" in raw and raw.count("十") == 1:
            tens, units = raw.split("十")
            if (not tens or tens in digits) and (not units or units in digits):
                return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return None


def resolve_reference(query: str, references: list[dict[str, Any]], page_context: dict | None = None) -> dict[str, Any]:
    """Resolve explicit ordinals and batch references, or request clarification."""
    ordinal = _ordinal(query)
    inherit_conditions = "继续按上次条件" in query
    batch = any(term in query for term in ("刚才那批", "这批股票", "上面那批", "继续按上次条件"))
    if ordinal is None and not batch:
        return {"status": "none"}
    valid = [item for item in references if item.get("committed_status") == "committed"]
    if not valid:
        return {"status": "clarify", "clarification": "尚无已提交的股票列表，请明确股票或先查询名单。"}
    # References are stored chronologically; several lists in the latest run are
    # ambiguous unless they denote exactly the same ordered set and date.
    latest_run = valid[-1].get("source_run_id")
    latest = [item for item in valid if item.get("source_run_id") == latest_run]
    distinct = {(tuple(item.get("ordered_symbols", [])), item.get("trade_date")) for item in latest}
    if len(distinct) != 1 or (ordinal is not None and not all(item.get("ordinal_resolvable") for item in latest)):
        return {"status": "clarify", "clarification": "上一轮包含多组名单或未能确认展示顺序，请明确股票代码或名单。"}
    if inherit_conditions:
        scopes = [_query_scope(item.get("query_scope"), item.get("source_tool")) for item in latest]
        if any(scope is None for scope in scopes):
            return {"status": "clarify", "clarification": "上一份结果未保留可核验的查询条件，请重新说明筛选条件。"}
        if len({_scope_identity(scope) for scope in scopes}) != 1:
            return {"status": "clarify", "clarification": "上一轮查询使用了不同条件，请明确要继续哪组条件。"}
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
    return {"status": "resolved", "reference_id": selected["reference_id"],
            "symbols": list(symbols), "trade_date": selected.get("trade_date"),
            "filters": selected.get("filters") if scope is not None else None,
            "sort_rules": selected.get("sort_rules") if scope is not None else None,
            "query_scope": scope, "query_scope_status": "known" if scope is not None else "unknown",
            "source_run_id": latest_run}
