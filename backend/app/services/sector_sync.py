"""After-close sector sync: catalog, daily metrics and lazy per-sector caches."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import akshare as ak

from app.collectors.hithink_finance_collector import HithinkFinanceCollector
from app.collectors.network import without_proxy
from app.collectors.sector_collector import collect_concept_spot
from app.repositories.sector_repository import SectorRepository
from app.services.sector_resolution import SectorMatch, normalize_sector_name


CATALOG_TTL = timedelta(hours=24)
CONSTITUENTS_TTL = timedelta(days=7)
HISTORY_LOOKBACK_DAYS = 60
INDUSTRY_SUMMARY_SOURCE = "akshare-ths-industry-summary"
INDUSTRY_HISTORY_SOURCE = "akshare-ths-industry-history"


class SectorSyncError(RuntimeError):
    """A primary sector source failed before anything could be persisted."""


def sync_catalog(
    repository: SectorRepository, *, force: bool = False,
    collector: HithinkFinanceCollector | None = None,
) -> dict[str, Any]:
    """Refresh the canonical 320-industry / 390-concept directory."""

    updated = repository.catalog_updated_at()
    if not force and updated is not None and _utcnow() - updated < CATALOG_TTL:
        return {"synced": False, "count": len(repository.catalog())}
    active = collector or HithinkFinanceCollector()
    industry = active.collect_index_catalog("industry")
    concept = active.collect_index_catalog("cn_concept")
    summary_codes = _ths_summary_codes()
    # A failed summary-name request must not erase the previous 90-industry flag.
    existing = {
        item["thscode"]: bool(item.get("in_summary")) for item in repository.catalog()
    }
    rows = [
        {"thscode": item.thscode, "name": item.name, "category": item.category,
         "in_summary": item.thscode in summary_codes if summary_codes
         else existing.get(item.thscode, False)}
        for item in (*industry, *concept)
    ]
    repository.upsert_catalog(rows)
    if summary_codes:
        repository.replace_summary_flags(sorted(summary_codes))
    return {"synced": True, "count": len(rows), "summary_count": len(summary_codes)}


def sync_industry_daily(
    repository: SectorRepository, trade_date: date,
) -> dict[str, Any]:
    """Store the 90-industry ranking from one Tonghuashun summary request."""

    catalog = repository.catalog("industry")
    if not catalog:
        sync_catalog(repository)
        catalog = repository.catalog("industry")
    by_name = {normalize_sector_name(item["name"]): item for item in catalog}
    frame = _industry_summary()
    rows, matched = [], []
    for record in frame.to_dict(orient="records"):
        name = str(record.get("板块") or "").strip()
        item = by_name.get(normalize_sector_name(name))
        if item is None:
            continue
        matched.append(item["thscode"])
        rows.append({
            "trade_date": trade_date,
            "thscode": item["thscode"],
            "change_pct": _number(record.get("涨跌幅")),
            "amount_yi": _number(record.get("总成交额")),
            "net_inflow_yi": _number(record.get("净流入")),
            "up_count": _integer(record.get("上涨家数")),
            "down_count": _integer(record.get("下跌家数")),
            "leader_name": _text(record.get("领涨股")),
            "leader_price": _number(record.get("领涨股-最新价")),
            "leader_change_pct": _number(record.get("领涨股-涨跌幅")),
            "source": INDUSTRY_SUMMARY_SOURCE,
            "captured_at": _utcnow(),
        })
    if not rows:
        raise SectorSyncError("industry summary did not match the sector catalog")
    repository.replace_summary_flags(matched)
    repository.upsert_daily_snapshot(rows)
    return {
        "trade_date": trade_date.isoformat(), "rows": len(rows),
        "source_rows": len(frame), "source": INDUSTRY_SUMMARY_SOURCE,
    }


def sync_concept_daily(
    repository: SectorRepository, trade_date: date,
) -> dict[str, Any]:
    """Store the whole concept ranking from the batched Tonghuashun CLI snapshot."""

    catalog = repository.catalog("cn_concept")
    if not catalog:
        sync_catalog(repository)
        catalog = repository.catalog("cn_concept")
    by_name = {normalize_sector_name(item["name"]): item for item in catalog}
    rows = []
    for record in collect_concept_spot():
        item = by_name.get(normalize_sector_name(record.sector_name))
        if item is None:
            continue
        rows.append({
            "trade_date": trade_date,
            "thscode": item["thscode"],
            "change_pct": record.change_pct,
            "amount_yi": record.amount_yi,
            "net_inflow_yi": record.net_inflow_yi,
            "up_count": record.up_count,
            "down_count": record.down_count,
            "leader_name": record.leader_name,
            "leader_price": record.leader_price,
            "leader_change_pct": record.leader_change_pct,
            "source": record.source,
            "captured_at": _utcnow(),
        })
    if not rows:
        raise SectorSyncError("concept snapshot did not match the sector catalog")
    repository.upsert_daily_snapshot(rows)
    return {
        "trade_date": trade_date.isoformat(), "rows": len(rows),
        "source": rows[0]["source"],
    }


def sync_sector_daily(
    repository: SectorRepository, trade_date: date, *,
    include_concepts: bool = True, force_catalog: bool = False,
) -> dict[str, Any]:
    """Run the cheap industry summary and the optional concept ranking sync."""

    sync_catalog(repository, force=force_catalog)
    result: dict[str, Any] = {"trade_date": trade_date.isoformat()}
    result["industry"] = sync_industry_daily(repository, trade_date)
    if include_concepts:
        try:
            result["concept"] = sync_concept_daily(repository, trade_date)
        except Exception as error:  # noqa: BLE001 - industry data is still valid
            result["concept"] = {"error": f"{type(error).__name__}: {error}"}
    return result


def ensure_constituents(
    repository: SectorRepository, member: SectorMatch, *,
    collector: HithinkFinanceCollector | None = None,
    max_age: timedelta = CONSTITUENTS_TTL,
) -> list[dict[str, Any]]:
    """Read the constituent cache, refreshing one sector when stale."""

    updated = repository.constituents_updated_at(member.thscode)
    cached = repository.constituents(member.thscode)
    if cached and updated is not None and _utcnow() - updated < max_age:
        return cached
    active = collector or HithinkFinanceCollector()
    rows = active.collect_index_constituents_by_code(member.thscode)
    if not rows:
        raise SectorSyncError(f"{member.name} 未返回成分股")
    repository.replace_constituents(
        member.thscode, [{"symbol": item.symbol, "name": item.name} for item in rows],
    )
    return repository.constituents(member.thscode)


def ensure_history(
    repository: SectorRepository, member: SectorMatch, end_date: date, *,
    days: int = 40, collector: HithinkFinanceCollector | None = None,
) -> list[dict[str, Any]]:
    """Read stored daily closes, backfilling one sector when they are insufficient."""

    wanted = max(days + 20, 30)
    cached = repository.daily_history(member.thscode, end_date, wanted)
    if len(cached) >= days + 5 and cached[-1]["trade_date"] >= end_date - timedelta(days=5):
        return cached
    active = collector or HithinkFinanceCollector()
    start = end_date - timedelta(days=HISTORY_LOOKBACK_DAYS)
    rows = None
    if member.category == "industry":
        try:
            rows = _akshare_industry_history(member.name, start, end_date)
        except Exception:  # noqa: BLE001 - fall back to the identity-preserving CLI
            rows = None
    if rows is None:
        rows = _hithink_history(active, member.thscode, start, end_date)
    if not rows:
        raise SectorSyncError(f"{member.name} 没有可用日线")
    repository.upsert_daily_history([
        {"trade_date": item["trade_date"], "thscode": member.thscode,
         "close": item["close"], "change_pct": item["change_pct"], "source": item["source"]}
        for item in rows
    ])
    return repository.daily_history(member.thscode, end_date, wanted)


def _industry_summary():
    try:
        with without_proxy():
            return ak.stock_board_industry_summary_ths()
    except Exception as error:  # noqa: BLE001 - surface a stable sync error
        raise SectorSyncError(f"industry summary failed: {type(error).__name__}") from error


def _ths_summary_codes() -> set[str]:
    """Read the 90-industry identity list; failures leave flags untouched."""

    try:
        with without_proxy():
            frame = ak.stock_board_industry_name_ths()
        return {
            f"{str(record['code']).strip()}.TI"
            for record in frame.to_dict(orient="records")
            if str(record.get("code") or "").strip()
        }
    except Exception:  # noqa: BLE001 - the daily summary can mark flags later
        return set()


def _akshare_industry_history(name: str, start: date, end: date) -> list[dict[str, Any]]:
    prelude = start - timedelta(days=14)
    with without_proxy():
        frame = ak.stock_board_industry_index_ths(
            symbol=name,
            start_date=prelude.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    return _history_rows(
        [
            (str(record.get("日期")), _number(record.get("收盘价")))
            for record in frame.to_dict(orient="records")
        ],
        start=start, end=end, source=INDUSTRY_HISTORY_SOURCE,
    )


def _hithink_history(
    collector: HithinkFinanceCollector, thscode: str, start: date, end: date,
) -> list[dict[str, Any]]:
    history = collector.collect_index_history(
        thscode, start_date=start - timedelta(days=14), end_date=end,
    )
    return _history_rows(
        [(item.trade_date.isoformat(), item.close) for item in history.items],
        start=start, end=end, source=f"hithink-finance.index.history.{thscode}",
    )


def _history_rows(
    values: list[tuple[str, float | None]], *, start: date, end: date, source: str,
) -> list[dict[str, Any]]:
    rows, previous = [], None
    for raw_date, close in values:
        try:
            trade_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        if close is None:
            continue
        change = None
        if previous is not None and previous != 0:
            change = round((close - previous) / previous * 100, 2)
        if start <= trade_date <= end:
            rows.append({"trade_date": trade_date, "close": close,
                         "change_pct": change, "source": source})
        previous = close
    return sorted(rows, key=lambda item: item["trade_date"])


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text in {"", "-", "--", "—"} else text


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
