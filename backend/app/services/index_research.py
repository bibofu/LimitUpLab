"""Index search, quote and constituent facts for the Agent tool surface.

The CLI exposes three distinct paths: ``index catalog`` for Tonghuashun
category indices, ``symbol search`` for name/code resolution (including broad
exchange indices), and ``index snapshot/history/constituents`` for any resolved
index. Constituents contain only code and name; no weight or industry data is
invented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.collectors.hithink_finance_collector import (
    HithinkDailyBarFact,
    HithinkFinanceCollector,
    HithinkIndexSearchFact,
)


CATALOG_CATEGORIES = frozenset({"industry", "cn_concept", "region", "tszs"})
CATEGORY_LABELS = {
    "industry": "同花顺行业指数",
    "cn_concept": "同花顺概念指数",
    "region": "地域指数",
    "tszs": "同花顺特色指数",
    "broad": "宽基/交易所指数",
    "all": "全部指数",
}
SHANGHAI = timezone(timedelta(hours=8))
SOURCE = "hithink-finance.index"
MAX_SEARCH_LIMIT = 50
MAX_QUOTE_DAYS = 250
MAX_CONSTITUENTS = 2000


@dataclass(frozen=True)
class IndexResolution:
    """One deterministic index identity plus same-name variants."""

    thscode: str
    ticker: str
    name: str
    exchange: str
    asset_type: str
    category: str
    variants: list[dict[str, Any]]
    candidates: list[dict[str, Any]]


def search_indices(
    *,
    query: str,
    category: str | None = None,
    limit: int = 20,
    collector: HithinkFinanceCollector | None = None,
) -> dict[str, Any]:
    """Resolve index names/codes and return the candidate set with a top match."""

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query is required")
    normalized_category = (category or "all").strip().lower()
    if normalized_category not in {*CATALOG_CATEGORIES, "broad", "all"}:
        raise ValueError("category must be industry, cn_concept, region, tszs, broad or all")
    requested_limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    active_collector = collector or HithinkFinanceCollector()
    facts = active_collector.collect_index_search(
        normalized_query, limit=MAX_SEARCH_LIMIT,
    )
    rows = [_search_row(fact) for fact in facts]
    if normalized_category in CATALOG_CATEGORIES:
        catalog = {
            item.thscode: item.category
            for item in active_collector.collect_index_catalog(normalized_category)
        }
        rows = [
            {**row, "category": catalog[row["thscode"]]}
            for row in rows
            if row["thscode"] in catalog
        ]
    elif normalized_category == "broad":
        rows = [
            row for row in rows
            if row["category"] in {"exchange_index", "csi_index"}
        ]
    ranked = _rank_rows(normalized_query, rows)
    returned = ranked[:requested_limit]
    resolved = returned[0] if returned else None
    variants = (
        [row for row in ranked if row["name"] == resolved["name"] and row["thscode"] != resolved["thscode"]]
        if resolved else []
    )
    data_missing = [] if returned else ["hithink_finance:no_matching_index"]
    warnings = []
    if resolved and _match_base(normalized_query, resolved) > 0:
        top_base = _match_base(normalized_query, resolved)
        equally_matched = [
            row for row in ranked
            if _match_base(normalized_query, row) == top_base
        ]
        if len({row["name"] for row in equally_matched}) > 1:
            warnings.append("存在多个同等匹配的指数；请结合category或thscode确认后再查询行情或成分股。")
    return {
        "query": normalized_query,
        "category": normalized_category,
        "category_label": CATEGORY_LABELS[normalized_category],
        "matched_count": len(ranked),
        "returned_count": len(returned),
        "resolved": resolved,
        "variants": variants,
        "rows": returned,
        "complete": len(ranked) <= requested_limit,
        "data_missing": data_missing,
        "warnings": warnings,
        "summary": _search_summary(normalized_query, ranked, resolved, variants),
    }


def build_index_quote(
    *,
    index: str,
    days: int | None = None,
    end_date: str | None = None,
    collector: HithinkFinanceCollector | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one index snapshot and, when requested, a bounded daily history."""

    active_collector = collector or HithinkFinanceCollector()
    resolution = resolve_index(index, collector=active_collector)
    snapshot = active_collector.collect_index_snapshot(resolution.thscode)
    requested_end_date = (
        date.fromisoformat(end_date)
        if end_date
        else (snapshot.captured_at.date() if snapshot is not None else date.today())
    )
    requested_days = None if days is None else max(2, min(days, MAX_QUOTE_DAYS))
    data_missing: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    start_date = requested_end_date
    data_as_of = snapshot.captured_at.date() if snapshot is not None else None
    data_fresh = False
    start_close = end_close = return_pct = change_pct = None
    max_drawdown_pct = None
    max_drawdown_peak_date = max_drawdown_trough_date = None
    positive_days = negative_days = flat_days = 0
    if snapshot is None:
        data_missing.append("hithink_finance:no_index_snapshot")
    else:
        data_fresh = snapshot.captured_at.date() == requested_end_date
    if requested_days is not None:
        start_date = requested_end_date - timedelta(days=max(requested_days * 3, 30))
        history = active_collector.collect_index_history(
            resolution.thscode, start_date=start_date, end_date=requested_end_date,
        )
        items = [
            item for item in history.items
            if item.trade_date <= requested_end_date
        ]
        items = items[-requested_days:]
        rows = _history_rows(items)
        if len(items) < requested_days:
            data_missing.append("hithink_finance:insufficient_index_history")
        if len(items) >= 2:
            stats = _window_statistics(items)
            start_close = stats["start_close"]
            end_close = stats["end_close"]
            return_pct = stats["return_pct"]
            change_pct = stats["change_pct"]
            max_drawdown_pct = stats["max_drawdown_pct"]
            max_drawdown_peak_date = stats["max_drawdown_peak_date"]
            max_drawdown_trough_date = stats["max_drawdown_trough_date"]
            positive_days = stats["positive_days"]
            negative_days = stats["negative_days"]
            flat_days = stats["flat_days"]
        if items:
            data_as_of = items[-1].trade_date
            data_fresh = data_as_of == requested_end_date
        else:
            data_fresh = False
        if data_as_of is not None and data_as_of != requested_end_date:
            warnings.append(
                f"请求截止日{requested_end_date.isoformat()}没有该指数日线，"
                f"实际数据截止{data_as_of.isoformat()}；可能为非交易日、尚未更新或来源缺口。"
            )
    complete = (
        not data_missing
        and (snapshot is not None or requested_days is not None)
        and (requested_days is None or len(rows) == requested_days)
    )
    return {
        "index_name": resolution.name,
        "thscode": resolution.thscode,
        "category": resolution.category,
        "variants": resolution.variants,
        "requested_days": requested_days,
        "requested_end_date": requested_end_date.isoformat(),
        "start_date": start_date.isoformat() if requested_days is not None else None,
        "data_as_of": data_as_of.isoformat() if data_as_of is not None else None,
        "data_fresh": data_fresh,
        "snapshot_last_price": snapshot.last_price if snapshot else None,
        "snapshot_change_pct": snapshot.change_pct if snapshot else None,
        "snapshot_open": snapshot.open_price if snapshot else None,
        "snapshot_high": snapshot.high_price if snapshot else None,
        "snapshot_low": snapshot.low_price if snapshot else None,
        "snapshot_prev_close": snapshot.prev_price if snapshot else None,
        "snapshot_volume": snapshot.volume if snapshot else None,
        "snapshot_turnover": snapshot.turnover if snapshot else None,
        "snapshot_captured_at": snapshot.captured_at.isoformat() if snapshot else None,
        "start_close": start_close,
        "end_close": end_close,
        "return_pct": return_pct,
        "change_pct": change_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_peak_date": max_drawdown_peak_date.isoformat() if max_drawdown_peak_date else None,
        "max_drawdown_trough_date": max_drawdown_trough_date.isoformat() if max_drawdown_trough_date else None,
        "positive_days": positive_days,
        "negative_days": negative_days,
        "flat_days": flat_days,
        "rows": rows,
        "complete": complete,
        "source": SOURCE,
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(),
        "data_missing": data_missing,
        "warnings": warnings,
        "summary": _quote_summary(
            resolution=resolution,
            snapshot=snapshot,
            requested_days=requested_days,
            requested_end_date=requested_end_date,
            data_as_of=data_as_of,
            return_pct=return_pct,
            end_close=end_close,
            max_drawdown_pct=max_drawdown_pct,
            positive_days=positive_days,
            negative_days=negative_days,
        ),
    }


def build_index_constituents(
    *,
    index: str,
    limit: int = 1000,
    collector: HithinkFinanceCollector | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one index's constituent list with explicit coverage boundaries."""

    active_collector = collector or HithinkFinanceCollector()
    resolution = resolve_index(index, collector=active_collector)
    requested_limit = max(1, min(limit, MAX_CONSTITUENTS))
    facts = active_collector.collect_index_constituents_by_code(resolution.thscode)
    rows = [
        {"symbol": fact.symbol, "thscode": fact.thscode, "name": fact.name}
        for fact in sorted(facts, key=lambda item: item.symbol)
    ]
    returned = rows[:requested_limit]
    truncated = len(rows) > requested_limit
    data_missing = [] if returned else ["hithink_finance:no_constituents"]
    warnings = [
        "来源仅返回成分股代码和名称，不含权重、行业分类和市值；不得自行补造。",
    ]
    if truncated:
        warnings.append(
            f"请求上限{requested_limit}只，来源共{len(rows)}只；本次未返回其余{len(rows) - requested_limit}只。"
        )
    return {
        "index_name": resolution.name,
        "thscode": resolution.thscode,
        "category": resolution.category,
        "variants": resolution.variants,
        "member_count": len(rows),
        "returned_count": len(returned),
        "complete": not truncated and bool(returned),
        "truncated": truncated,
        "has_weights": False,
        "rows": returned,
        "source": SOURCE,
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(),
        "data_missing": data_missing,
        "warnings": warnings,
        "summary": _constituents_summary(resolution, len(rows), len(returned), truncated),
    }


def resolve_index(
    query: str,
    *,
    collector: HithinkFinanceCollector | None = None,
) -> IndexResolution:
    """Resolve one index name or code to a deterministic thscode."""

    normalized = query.strip()
    if not normalized:
        raise ValueError("index is required")
    active_collector = collector or HithinkFinanceCollector()
    facts = active_collector.collect_index_search(normalized, limit=MAX_SEARCH_LIMIT)
    rows = _rank_rows(normalized, [_search_row(fact) for fact in facts])
    if not rows:
        raise ValueError(f"Cannot resolve index from: {normalized}")
    top = rows[0]
    variants = [
        row for row in rows
        if row["name"] == top["name"] and row["thscode"] != top["thscode"]
    ]
    top_base = _match_base(normalized, top)
    if top_base > 0:
        equally_matched = [
            row for row in rows
            if _match_base(normalized, row) == top_base
        ]
        if len({row["name"] for row in equally_matched}) > 1:
            candidates = "、".join(
                f"{row['name']}({row['thscode']})"
                for row in equally_matched[:5]
            )
            raise ValueError(
                f"Ambiguous index: {normalized}; candidates include {candidates}. "
                "Use index_search first and pass a specific thscode."
            )
    return IndexResolution(
        thscode=top["thscode"],
        ticker=top["ticker"],
        name=top["name"],
        exchange=top["exchange"],
        asset_type=top["asset_type"],
        category=top["category"],
        variants=variants,
        candidates=rows,
    )


def _search_row(fact: HithinkIndexSearchFact) -> dict[str, Any]:
    return {
        "thscode": fact.thscode,
        "ticker": fact.ticker,
        "name": fact.name,
        "exchange": fact.exchange,
        "asset_type": fact.asset_type,
        "category": _category(fact.thscode),
        "list_date": fact.list_date.isoformat() if fact.list_date else None,
    }


def _category(thscode: str) -> str:
    code = thscode.upper()
    if code.endswith(".TI"):
        return "ths_index"
    if code.endswith(".CSI"):
        return "csi_index"
    if code.endswith((".SH", ".SZ", ".BJ")):
        return "exchange_index"
    return "index"


def _match_base(query: str, row: dict[str, Any]) -> int:
    """Classify a candidate as exact, contained, containing or unrelated."""

    normalized = query.replace(" ", "").casefold()
    name = row["name"].replace(" ", "").casefold()
    ticker = str(row["ticker"]).casefold()
    thscode = row["thscode"].casefold()
    if normalized in {name, ticker, thscode}:
        return 0
    if normalized in name:
        return 1
    if name in normalized:
        return 2
    return 3


def _rank_rows(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank exact names/codes before contained and unrelated matches."""

    normalized = query.replace(" ", "").casefold()
    def key(row: dict[str, Any]) -> tuple[int, int, int, str]:
        base = _match_base(query, row)
        name = row["name"].replace(" ", "").casefold()
        thscode = row["thscode"].casefold()
        exchange_preference = 0
        if base == 0 and name == normalized:
            if any(token in name for token in ("深证", "创业板", "中小")) and thscode.endswith(".sz"):
                exchange_preference = -1
            elif thscode.endswith(".sh"):
                exchange_preference = -1
        return (base, exchange_preference, -len(name), thscode)
    return sorted(rows, key=key)


def _history_rows(items: list[HithinkDailyBarFact]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_close = None
    for item in items:
        change_pct = (
            round((item.close - previous_close) / previous_close * 100, 2)
            if previous_close and previous_close > 0 else None
        )
        rows.append({
            "trade_date": item.trade_date.isoformat(),
            "open": item.open,
            "high": item.high,
            "low": item.low,
            "close": item.close,
            "volume": item.volume,
            "turnover": item.turnover,
            "change_pct": change_pct,
        })
        previous_close = item.close
    return rows


def _window_statistics(items: list[HithinkDailyBarFact]) -> dict[str, Any]:
    closes = [item.close for item in items]
    start_close = closes[0]
    end_close = closes[-1]
    daily_changes = [
        (closes[index] - closes[index - 1]) / closes[index - 1] * 100
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    peak_value = items[0].close
    peak_date = items[0].trade_date
    max_drawdown = 0.0
    max_peak_date = peak_date
    max_trough_date = peak_date
    for item in items:
        if item.close > peak_value:
            peak_value = item.close
            peak_date = item.trade_date
        if peak_value > 0:
            drawdown = (item.close - peak_value) / peak_value * 100
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                max_peak_date = peak_date
                max_trough_date = item.trade_date
    return {
        "start_close": round(start_close, 4),
        "end_close": round(end_close, 4),
        "return_pct": round((end_close - start_close) / start_close * 100, 2) if start_close else None,
        "change_pct": round((end_close - closes[-2]) / closes[-2] * 100, 2) if len(closes) > 1 and closes[-2] else None,
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_peak_date": max_peak_date,
        "max_drawdown_trough_date": max_trough_date,
        "positive_days": sum(value > 0 for value in daily_changes),
        "negative_days": sum(value < 0 for value in daily_changes),
        "flat_days": sum(value == 0 for value in daily_changes),
    }


def _search_summary(
    query: str,
    ranked: list[dict[str, Any]],
    resolved: dict[str, Any] | None,
    variants: list[dict[str, Any]],
) -> str:
    if not resolved:
        return f"未找到与“{query}”匹配的A股指数。"
    variant_text = ""
    if variants:
        variant_text = "；同代码变体：" + "、".join(
            f"{row['thscode']}" for row in variants
        )
    return (
        f"与“{query}”匹配的指数共{len(ranked)}个，优先解析为"
        f"{resolved['name']}({resolved['thscode']}){variant_text}。"
    )


def _quote_summary(
    *,
    resolution: IndexResolution,
    snapshot: Any,
    requested_days: int | None,
    requested_end_date: date,
    data_as_of: date | None,
    return_pct: float | None,
    end_close: float | None,
    max_drawdown_pct: float | None,
    positive_days: int,
    negative_days: int,
) -> str:
    parts = [f"{resolution.name}({resolution.thscode})"]
    if snapshot is not None:
        change = (
            f"{snapshot.change_pct:+.2f}%"
            if snapshot.change_pct is not None else "涨跌幅未知"
        )
        parts.append(
            f"最新快照{snapshot.last_price}（{change}，"
            f"{snapshot.captured_at.isoformat()}）"
        )
    else:
        parts.append("未取得最新快照")
    if requested_days is not None:
        if data_as_of is None or end_close is None:
            parts.append("未取得可用日线历史")
        else:
            return_text = f"{return_pct:+.2f}%" if return_pct is not None else "未知"
            drawdown_text = (
                f"{max_drawdown_pct:.2f}%" if max_drawdown_pct is not None else "未知"
            )
            parts.append(
                f"最近{requested_days}个交易日截至{data_as_of.isoformat()}，"
                f"区间涨跌{return_text}，收于{end_close}，"
                f"最大回撤{drawdown_text}，"
                f"上涨/下跌{positive_days}/{negative_days}天"
            )
            if data_as_of != requested_end_date:
                parts.append(f"请求截止日{requested_end_date.isoformat()}无日线")
    return "；".join(parts) + "。"


def _constituents_summary(
    resolution: IndexResolution,
    member_count: int,
    returned_count: int,
    truncated: bool,
) -> str:
    if not member_count:
        return f"{resolution.name}({resolution.thscode}) 未返回成分股。"
    suffix = f"（请求上限截断，来源共{member_count}只）" if truncated else ""
    return (
        f"{resolution.name}({resolution.thscode}) 返回{returned_count}只成分股{suffix}；"
        "来源仅提供代码和名称，不含权重、行业和市值。"
    )
