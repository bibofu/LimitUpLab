"""Build Agent-ready industry-sector performance facts on demand."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from app.collectors.sector_collector import (
    SectorDailyRow,
    SectorSpotRow,
    collect_sector_history,
    collect_sector_spot,
)
from app.models import (
    SectorHistoryPoint,
    SectorPerformanceFacts,
    SectorRankingItem,
)


SpotCollector = Callable[[], list[SectorSpotRow]]
HistoryCollector = Callable[[str, date, date], list[SectorDailyRow]]


def build_sector_performance(
    sector: str | None = None,
    trade_date: date | None = None,
    *,
    spot_collector: SpotCollector = collect_sector_spot,
    history_collector: HistoryCollector = collect_sector_history,
) -> SectorPerformanceFacts:
    """Return latest ranking context and optional single-sector trend facts."""

    target_date = trade_date or date.today()
    spot_rows = sorted(spot_collector(), key=lambda item: item.rank)
    if not spot_rows:
        raise ValueError("sector provider returned no ranking rows")

    current_snapshot = trade_date is None or target_date == date.today()
    selected = _resolve_sector(sector, spot_rows) if sector else None
    history: list[SectorDailyRow] = []
    history_error: Exception | None = None
    if selected is not None:
        try:
            history = [
                item
                for item in history_collector(
                    selected.sector_name,
                    target_date - timedelta(days=50),
                    target_date,
                )
                if item.trade_date <= target_date
            ]
        except Exception as error:  # noqa: BLE001
            history_error = error

    latest_history = history[-1] if history else None
    data_as_of = target_date if current_snapshot else (
        latest_history.trade_date if latest_history else target_date
    )
    sources = list(
        dict.fromkeys(
            [
                selected.source if selected else spot_rows[0].source,
                *([latest_history.source] if latest_history else []),
            ]
        )
    )
    if history_error is not None:
        sources.append("sector-history-unavailable")

    return SectorPerformanceFacts(
        requested_sector=sector,
        sector_name=selected.sector_name if selected else None,
        trade_date=target_date,
        data_as_of=data_as_of,
        data_fresh=current_snapshot or data_as_of == target_date,
        rank=selected.rank if selected and current_snapshot else None,
        sector_count=len(spot_rows),
        change_pct=(
            selected.change_pct
            if selected and current_snapshot
            else (latest_history.change_pct if latest_history else None)
        ),
        amount_yi=selected.amount_yi if selected and current_snapshot else None,
        net_inflow_yi=(
            selected.net_inflow_yi if selected and current_snapshot else None
        ),
        up_count=selected.up_count if selected and current_snapshot else None,
        down_count=selected.down_count if selected and current_snapshot else None,
        leader_name=selected.leader_name if selected and current_snapshot else None,
        leader_price=selected.leader_price if selected and current_snapshot else None,
        leader_change_pct=(
            selected.leader_change_pct if selected and current_snapshot else None
        ),
        return_5d_pct=_period_return(history, 5),
        return_20d_pct=_period_return(history, 20),
        top_sectors=[_ranking_item(item) for item in spot_rows[:5]]
        if current_snapshot
        else [],
        bottom_sectors=[_ranking_item(item) for item in spot_rows[-5:][::-1]]
        if current_snapshot
        else [],
        history=[
            SectorHistoryPoint(
                trade_date=item.trade_date,
                close=item.close,
                change_pct=item.change_pct,
            )
            for item in history[-20:]
        ],
        sources=sources,
    )


def _resolve_sector(query: str, rows: list[SectorSpotRow]) -> SectorSpotRow:
    normalized = _normalize_sector_name(query)
    exact = [item for item in rows if _normalize_sector_name(item.sector_name) == normalized]
    if exact:
        return exact[0]
    contains = [
        item
        for item in rows
        if normalized in _normalize_sector_name(item.sector_name)
        or _normalize_sector_name(item.sector_name) in normalized
    ]
    if len(contains) == 1:
        return contains[0]
    if contains:
        return min(contains, key=lambda item: len(item.sector_name))
    raise ValueError(f"未找到与“{query}”匹配的行业板块")


def _normalize_sector_name(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    for term in ("今天", "今日", "a股", "板块", "行业", "概念", "相关"):
        normalized = normalized.replace(term, "")
    return normalized


def _period_return(rows: list[SectorDailyRow], days: int) -> float | None:
    if len(rows) <= days or rows[-days - 1].close == 0:
        return None
    return round((rows[-1].close - rows[-days - 1].close) / rows[-days - 1].close * 100, 2)


def _ranking_item(row: SectorSpotRow) -> SectorRankingItem:
    return SectorRankingItem(
        sector_name=row.sector_name,
        rank=row.rank,
        change_pct=row.change_pct,
        amount_yi=row.amount_yi,
        up_count=row.up_count,
        down_count=row.down_count,
        leader_name=row.leader_name,
        leader_change_pct=row.leader_change_pct,
    )
