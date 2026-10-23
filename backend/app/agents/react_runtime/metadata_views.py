"""Declared metadata collections become bounded, addressable evidence views."""

from copy import deepcopy
from typing import TypedDict


PREVIEW_ROWS = 5
REPRESENTATIVE_ROWS = 3


class SectorSummaryRow(TypedDict):
    sector_name: str
    unique_stock_count: int
    limit_up_event_count: int
    trade_day_count: int
    rank: int
    representative_symbols: str
    representative_names: str
    representative_count: int
    stocks_count: int
    representatives_truncated: bool


def metadata_views(tool, payload):
    """Select tool-declared collections, never arbitrary model-provided paths."""
    if not isinstance(payload, dict):
        return {}
    if tool == "limit_up_events" and payload.get("group_by") in {"industry", "concept"}:
        groups = payload.get("sector_summary")
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            return {}
        rows = []
        for position, group in enumerate(groups, 1):
            stocks = group.get("stocks", [])
            if not isinstance(stocks, list) or any(not isinstance(stock, dict) for stock in stocks):
                return {}
            representatives = sorted(stocks, key=lambda stock: str(stock.get("symbol", "")))[:REPRESENTATIVE_ROWS]
            rows.append(SectorSummaryRow(
                sector_name=group.get("sector_name"), unique_stock_count=group.get("unique_stock_count"),
                limit_up_event_count=group.get("limit_up_event_count"), trade_day_count=group.get("trade_day_count"),
                rank=position,
                representative_symbols="、".join(str(stock.get("symbol", "")) for stock in representatives),
                representative_names="、".join(str(stock.get("name", "")) for stock in representatives),
                representative_count=len(representatives), stocks_count=len(stocks),
                representatives_truncated=len(stocks) > len(representatives),
            ))
        raw_contract = payload.get("sector_summary_contract")
        contract = deepcopy(raw_contract) if isinstance(raw_contract, dict) else {}
        # Older checkpoints have no group completeness proof. A preview cannot
        # upgrade their metadata into a complete group population. This contract
        # refers to all filtered events before the independent event-row limit.
        complete = (type(contract.get("group_count")) is int
                    and contract["group_count"] == len(rows)
                    and type(contract.get("returned_group_count")) is int
                    and contract["returned_group_count"] == len(rows)
                    and contract.get("groups_truncated") is False
                    and contract.get("group_by") == payload["group_by"]
                    and type(contract.get("population_event_count")) is int
                    and type(payload.get("matched_count")) is int
                    and contract["population_event_count"] == payload["matched_count"]
                    and type(contract.get("unclassified_event_count")) is int
                    and 0 <= contract["unclassified_event_count"] <= payload["matched_count"])
        unclassified = contract.get("unclassified_event_count")
        classification_missing = ([f"sector_summary: {unclassified} events lack {payload['group_by']} classification"]
                                  if type(unclassified) is int and unclassified > 0 else [])
        boundary = rows[PREVIEW_ROWS - 1] if len(rows) >= PREVIEW_ROWS else None
        return {"sector_summary": {
            "rows": rows, "source_truncated": not complete or bool(classification_missing),
            "independent_collection_complete": complete,
            "data_missing": classification_missing,
            "metadata": {
                "trade_date": payload.get("trade_date"), "start_trade_date": payload.get("start_trade_date"),
                "grouping": contract, "group_by": payload["group_by"],
                "classification_scope": "Groups cover classified events only; unclassified_event_count remains an explicit coverage gap.",
                "representative_selection": "At most three stocks per group, symbol ascending; these are representatives, not all members.",
                "preview_boundary": {
                    "unique_stock_count": boundary.get("unique_stock_count") if boundary else None,
                    "limit_up_event_count": boundary.get("limit_up_event_count") if boundary else None,
                    "tied_groups_in_returned_collection": sum(
                        (row.get("unique_stock_count"), row.get("limit_up_event_count")) ==
                        (boundary.get("unique_stock_count"), boundary.get("limit_up_event_count"))
                        for row in rows) if boundary else 0,
                    "all_groups_available": complete},
            },
        }}
    if tool == "stock_kline" and isinstance(payload.get("return_windows"), list):
        windows = payload["return_windows"]
        # No computable window is a missing statistic, not an empty result set
        # that should gain a separate current evidence identity.
        if not windows or any(not isinstance(window, dict) for window in windows):
            return {}
        return {"return_windows": {
            "rows": [{"symbol": payload.get("symbol"), **deepcopy(window)} for window in windows],
            "source_truncated": False,
            "metadata": {
                "symbol": payload.get("symbol"), "data_as_of": payload.get("data_as_of"),
                "calendar_verification": deepcopy(payload.get("calendar_verification") or {"status": "not_verified"}),
                "window_semantics": "Each row binds its observed dates, prices and interval count. Use a matching window directly; reading every bar is unnecessary and does not verify calendar completeness.",
            },
        }}
    return {}
