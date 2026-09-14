"""Small independent oracle over recorded raw rows, not production filter helpers."""

from decimal import Decimal


PREFIXES = {"main_board": ("600", "601", "603", "605", "000", "001", "002", "003"),
            "chinext": ("300", "301"), "star_market": ("688", "689")}
KEYS = {"closed_limit", "market", "board_height", "min_board_height", "min_break_count",
        "max_break_count", "symbol", "order_by", "descending", "take"}


def select_rows(rows, selection):
    if set(selection) - KEYS:
        raise ValueError("unknown selection fields")
    if "market" in selection and selection["market"] not in PREFIXES:
        raise ValueError("unsupported market")
    if "closed_limit" in selection and type(selection["closed_limit"]) is not bool:
        raise ValueError("closed_limit must be boolean")
    for key in ("board_height", "min_board_height", "min_break_count", "max_break_count", "take"):
        if key in selection and (type(selection[key]) is not int or selection[key] < (1 if key == "take" else 0)):
            raise ValueError("invalid integer selection")
    if "descending" in selection and type(selection["descending"]) is not bool:
        raise ValueError("descending must be boolean")
    if ("take" in selection or "descending" in selection) and "order_by" not in selection:
        raise ValueError("bounded selection requires explicit ordering")
    if "order_by" in selection and selection["order_by"] not in {"amount", "board_height", "break_count"}:
        raise ValueError("unsupported ordering")
    selected = []
    for row in rows:
        if any(row[k] != selection[k] for k in ("closed_limit", "board_height", "symbol") if k in selection):
            continue
        if "market" in selection and not row["symbol"].startswith(PREFIXES[selection["market"]]):
            continue
        if any(row[field] < selection[key] for key, field in
               (("min_board_height", "board_height"), ("min_break_count", "break_count")) if key in selection):
            continue
        if "max_break_count" in selection and row["break_count"] > selection["max_break_count"]:
            continue
        selected.append(row)
    selected.sort(key=lambda r: r["symbol"])
    if "order_by" in selection:
        selected.sort(key=lambda r: Decimal(str(r[selection["order_by"]])), reverse=selection.get("descending", True))
    return selected[:selection["take"]] if "take" in selection else selected


def complete_day(world, day):
    """Closed + failed partitions avoid the production 100-row limit on all events."""
    partitions = {}
    for recording in world.recordings:
        args, payload = recording.arguments, recording.observation.payload
        if recording.tool != "limit_up_events" or payload.get("trade_date") != day:
            continue
        if any(args.get(k) for k in ("query", "market", "board_height", "min_board_height", "highest_only", "group_by", "broken_only")):
            continue
        if args.get("recent_trade_days", 1) != 1:
            continue
        status = payload.get("event_status")
        rows = payload.get("events", [])
        if status not in {"closed", "failed"} or recording.observation.state not in {"ok", "empty"}:
            continue
        if payload.get("returned_count") != payload.get("matched_count") or len(rows) != payload.get("matched_count"):
            continue
        if any(r["trade_date"] != day or r["closed_limit"] != (status == "closed") for r in rows):
            raise ValueError("invalid baseline partition")
        indexed = {r["symbol"]: r for r in rows}
        if len(indexed) != len(rows) or (status in partitions and partitions[status] != indexed):
            raise ValueError("duplicate or conflicting baseline partition")
        partitions[status] = indexed
    if set(partitions) != {"closed", "failed"}:
        raise ValueError("both complete status partitions are required")
    if partitions["closed"].keys() & partitions["failed"].keys():
        raise ValueError("overlapping baseline partitions")
    return list(partitions["closed"].values()) + list(partitions["failed"].values())
