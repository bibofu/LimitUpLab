"""Tonghuashun pool membership with explicit Eastmoney field supplementation."""

from __future__ import annotations

from math import isfinite

from app.collectors.akshare_limit_up_collector import (
    LimitUpCollectionResult,
    _collect_closed_limit_up_events,
    _collect_failed_limit_up_events,
    parse_akshare_trade_date,
)
from app.collectors.hithink_finance_collector import HithinkFinanceCollector
from app.models import LimitUpEvent
from app.services.limit_up_reason import merge_limit_up_reasons


def collect_limit_up_events(trade_date: str) -> LimitUpCollectionResult:
    """Prefer THS independently for each pool; never invent missing scoring facts."""

    target = parse_akshare_trade_date(trade_date)
    collector = HithinkFinanceCollector()
    errors: list[str] = []
    warnings: list[str] = []
    sources: dict[str, str] = {}
    events: dict[str, LimitUpEvent] = {}
    closed_symbols: set[str] = set()
    complete_pools = 0
    closed_snapshot = None
    enriched_count = 0

    for closed, pool, remote_loader, supplement_loader in (
        (True, "closed_limit_pool", collector.collect_all_limit_up_pool,
         _collect_closed_limit_up_events),
        (False, "failed_limit_pool", collector.collect_all_limit_break_pool,
         _collect_failed_limit_up_events),
    ):
        try:
            snapshot = remote_loader(trade_date=target)
            if snapshot.trade_date != target:
                raise ValueError("pool trade date does not match request")
        except Exception as error:  # noqa: BLE001 - isolate provider/pool failures
            warnings.append(f"hithink-finance.{pool}: {error}; using Eastmoney fallback")
            try:
                fallback = supplement_loader(target, trade_date, strict=True)
            except Exception as fallback_error:  # noqa: BLE001
                errors.append(f"{pool}: both providers failed; {error}; {fallback_error}")
                sources[pool] = "unavailable"
                continue
            sources[pool] = "akshare-eastmoney.fallback"
            complete_pools += 1
            if closed:
                closed_symbols.update(item.symbol for item in fallback)
            for item in fallback:
                if closed or item.symbol not in closed_symbols:
                    events.setdefault(item.symbol, item)
            continue

        sources[pool] = "hithink-finance"
        if closed:
            closed_snapshot = snapshot
            closed_symbols.update(item.symbol for item in snapshot.items)
        # A successful empty THS pool is authoritative, including non-trading days.
        if not snapshot.items:
            complete_pools += 1
            continue
        try:
            supplemental = {
                item.symbol: item
                for item in supplement_loader(target, trade_date, strict=True)
            }
        except Exception as error:  # noqa: BLE001
            errors.append(f"{pool}: Eastmoney required field supplement failed: {error}")
            continue
        sources[pool] = "hithink-finance+akshare-eastmoney.fields"
        missing: list[str] = []
        pool_events: list[LimitUpEvent] = []
        for item in snapshot.items:
            if not closed and item.symbol in closed_symbols:
                warnings.append(f"{item.symbol}: in both THS pools; closed pool takes precedence")
                continue
            base = supplemental.get(item.symbol)
            if base is None:
                missing.append(item.symbol)
                continue
            updates: dict[str, object] = {"name": item.name, "closed_limit": closed}
            if closed:
                updates["board_height"] = item.board_height
            else:
                updates["board_height"] = 1
                # Keep genuine zero values, but use the supplement for absent facts.
                for field in ("break_count", "amount", "turnover_rate"):
                    value = getattr(item, field)
                    if value is not None:
                        if not isfinite(value) or value < 0 or (
                            field == "break_count" and int(value) != value
                        ):
                            errors.append(f"{pool}.{item.symbol}: invalid THS {field}")
                            break
                        updates[field] = value
                else:
                    updates["seal_count"] = max(int(updates.get("break_count", base.break_count)), 1)
                    pool_events.append(base.model_copy(update=updates))
                    continue
                continue
            pool_events.append(base.model_copy(update=updates))
        if closed:
            pool_events, enriched_count = merge_limit_up_reasons(pool_events, snapshot)
        if missing:
            errors.append(
                f"{pool}: missing required Eastmoney fields for THS symbols: {','.join(missing)}"
            )
        if len(pool_events) == len(snapshot.items):
            complete_pools += 1
        for event in pool_events:
            events.setdefault(event.symbol, event)

    payload = sorted(events.values(), key=lambda item: (
        item.trade_date, item.closed_limit, item.board_height, item.first_limit_time,
    ), reverse=True)
    status = (
        ("partial" if payload or complete_pools else "error") if errors
        else ("ok" if payload else "empty")
    )
    return LimitUpCollectionResult(
        status=status,
        data_fresh=bool(payload or complete_pools),
        source_errors=tuple(errors),
        payload=payload,
        pool_sources=sources,
        source_warnings=tuple(warnings),
        hithink_limit_up_snapshot=closed_snapshot,
        reason_enriched_count=enriched_count,
    )
