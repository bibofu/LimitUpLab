"""Canonical payload extraction shared by the production runner."""

from __future__ import annotations

from fastapi.encoders import jsonable_encoder

from app.agents.kline_presentation import kline_agent_view
from app.agents.tool_schemas_market import MARKET_TOOL_SCHEMAS
from app.agents.tool_schemas_news import NEWS_TOOL_SCHEMAS
from app.agents.tool_schemas_research import RESEARCH_TOOL_SCHEMAS
from app.agents.tool_schemas_review import REVIEW_TOOL_SCHEMAS

TOOL_COLLECTIONS = {
    contract.name: contract.collection
    for contract in (*MARKET_TOOL_SCHEMAS, *NEWS_TOOL_SCHEMAS,
                     *RESEARCH_TOOL_SCHEMAS, *REVIEW_TOOL_SCHEMAS)
    if contract.collection
}
COLLECTIONS = tuple(dict.fromkeys((
    "events", "top_candidates", "items", "stocks", "top_sectors", "bars", "candidates",
    *TOOL_COLLECTIONS.values(),
)))


def payload_of(result):
    """Return the structured payload, normalizing declared collection rows."""

    raw = jsonable_encoder(result.output)
    trace = jsonable_encoder(result.trace_output)
    collection = TOOL_COLLECTIONS.get(result.name)
    if isinstance(raw, list) and collection:
        raw = {**trace, collection: raw}
    outcome = result.trace().result
    if outcome is not None and outcome.status == "error":
        return raw if isinstance(raw, (dict, list)) else trace
    if result.name == "first_board_ratings" and isinstance(raw, dict):
        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("facts", {}), dict)
            for item in candidates
        ):
            raise ValueError("Invalid first_board_ratings collection: candidates must be object rows")
        raw = {**raw, "top_candidates": [
            {**item.get("facts", {}), **{key: value for key, value in item.items() if key != "facts"}}
            for item in candidates
        ]}
    if result.name == "stock_kline" and isinstance(raw, dict):
        raw = {**kline_agent_view(raw), "metric_definitions": {
            "change_pct": "Provider daily percentage only; null when unavailable. Do not replace with unadjusted close-to-close changes, especially on ex-right dates.",
            "close_to_close_pct": "(close / previous_close - 1)*100 using known unadjusted prices; comparison date is previous_trade_date. A price-change statistic, not the provider/ex-right daily change_pct.",
            "display_window_return": "Return over the displayed bars only, with explicit first/last date and close. N observations contain N-1 observed intervals; this does not prove calendar completeness.",
            "return_windows": "Only windows backed by visible first/last closes. Each entry states observations and observed_intervals. Missing observations require a longer query; never infer a start date from an unscoped percentage.",
            "trend": "Moving-average alignment label; not consecutive daily increases.",
            "max_drawdown_pct": "Peak-to-subsequent-trough in displayed bars, percent.",
        }}
    payload = raw if isinstance(raw, (dict, list)) else trace
    if collection:
        rows_of(payload, tool=result.name)
    return payload


def rows_of(payload, *, tool=None):
    """Return the declared row collection, or a payload-shaped single row."""

    collection = TOOL_COLLECTIONS.get(tool)
    if isinstance(payload, list):
        rows = payload
    elif collection:
        if not isinstance(payload, dict) or not isinstance(payload.get(collection), list):
            raise ValueError(f"Invalid {tool} collection: {collection} must be an array")
        rows = payload[collection]
    else:
        for key in COLLECTIONS:
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload] if payload else []
    if collection and any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid {tool} collection: {collection} must contain object rows")
    return rows
