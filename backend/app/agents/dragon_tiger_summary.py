"""Deterministic overview of the returned Dragon-Tiger records, by time window."""

from math import fsum, isfinite


FLOW_FIELDS = (
    "net_buy_amount", "organization_net_buy_amount", "hot_money_net_buy_amount",
)
OVERVIEW_RANK_LIMIT = 5


def _known_number(value):
    return type(value) in (int, float) and isfinite(value)


def _ranked(rows, field, *, positive):
    # Preserve separate disclosure records; symbol alone is not a record key.
    selected = [(index, row) for index, row in rows
                if (row[field] > 0 if positive else row[field] < 0)]
    selected.sort(key=lambda pair: (
        -pair[1][field] if positive else pair[1][field], pair[1]["symbol"], pair[0],
    ))
    prefix = selected[:OVERVIEW_RANK_LIMIT]
    cutoff = prefix[-1][1][field] if prefix else None
    return {
        "records": [{"row_index": index, "symbol": row["symbol"], "name": row["name"],
                     "amount": row[field]} for index, row in prefix],
        "omitted_cutoff_ties": sum(row[field] == cutoff for _, row in selected[len(prefix):]),
    }


def build_dragon_tiger_overview(payload, *, query=None):
    """No cross-window sums, inferred zeros, deduplication, or universe expansion."""
    items = payload["items"]
    matched = payload.get("matched_count")
    complete = not payload.get("source_truncated", False) and (
        matched is None or matched == len(items)
    )
    groups = {}
    for index, row in enumerate(items):
        days = row.get("range_days")
        key = str(days) if type(days) is int and days > 0 else "unknown"
        groups.setdefault(key, []).append((index, row))
    windows = {}
    for key, rows in groups.items():
        flows = {}
        for field in FLOW_FIELDS:
            known = [(index, row) for index, row in rows if _known_number(row.get(field))]
            flows[field] = {
                "available_count": len(known), "missing_count": len(rows) - len(known),
                "positive_count": sum(row[field] > 0 for _, row in known),
                "negative_count": sum(row[field] < 0 for _, row in known),
                "zero_count": sum(row[field] == 0 for _, row in known),
                "sum_known_amount": fsum(row[field] for _, row in known) if known and key != "unknown" else None,
                "net_buy_leaders": _ranked(known, field, positive=True),
                "net_sell_leaders": _ranked(known, field, positive=False),
            }
        windows[key] = {
            "range_days": None if key == "unknown" else int(key),
            "record_count": len(rows),
            "distinct_stock_count": len({row["symbol"] for _, row in rows}),
            "flows": flows,
        }
    return {
        "trade_date": payload.get("trade_date"), "source": payload.get("source"),
        "board_type": payload.get("board_type"), "query": query,
        "scope": "returned_records", "complete_matching_query": complete,
        "record_count": len(items),
        "distinct_stock_count": len({row["symbol"] for row in items}),
        "amount_unit": "元", "rank_limit": OVERVIEW_RANK_LIMIT,
        "definitions": (
            "按区间天数分别统计披露记录；股票数仅代码去重，原始金额记录与排名不按股票合并不同区间或多条披露。"
            "sum_known_amount仅为同一区间已知披露记录金额之和，不代表去重股票或全市场资金流向；"
            "正负记录数量多寡不代表金额合计方向，不能据此称整体净流入/流出。"
            "机构与游资字段是来源提供的分类，未证明二者穷尽全部席位。"
            "unknown区间不得称为单日榜，也不提供金额合计，因为无法证明记录区间一致。正数为净买、负数为净卖；缺失不当零，排名仅包含已知且符合方向的金额，"
            "缺失值存在时只能称已知金额记录中的排名。并列金额按代码、原始行序取前5，omitted_cutoff_ties为被省略的末位并列条数。"
            "row_index为items中从0开始的原记录位置。complete_matching_query=false时仅代表返回子集，不能称全榜统计或全榜排名；"
            "true也只覆盖本次board_type/query。普通概览可直接引用已计算的统计；其他筛选、统计或交表仍使用原始items。"
        ),
        "windows": windows,
    }
