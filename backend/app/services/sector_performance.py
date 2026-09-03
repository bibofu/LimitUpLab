"""Build Agent-ready industry-sector performance facts on demand."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from app.collectors.sector_collector import (
    SectorDailyRow,
    SectorSpotRow,
    collect_concept_history,
    collect_concept_spot,
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

_INDUSTRY_GROUP_ALIASES = {
    "军工": ("军工装备", "军工电子"),
    "国防军工": ("军工装备", "军工电子"),
}
_INDUSTRY_GROUP_LABELS = {
    "军工": "军工",
    "国防军工": "军工",
}
_CONCEPT_ALIASES = {
    "ai": "人工智能",
    "人工智能": "人工智能",
    "aigc": "AIGC概念",
}


def build_sector_performance(
    sector: str | None = None,
    trade_date: date | None = None,
    *,
    spot_collector: SpotCollector = collect_sector_spot,
    history_collector: HistoryCollector = collect_sector_history,
    concept_spot_collector: SpotCollector = collect_concept_spot,
    concept_history_collector: HistoryCollector = collect_concept_history,
) -> SectorPerformanceFacts:
    """Return latest industry/concept ranking and optional named-sector facts."""

    target_date = trade_date or date.today()
    spot_rows = sorted(spot_collector(), key=lambda item: item.rank)
    if not spot_rows:
        raise ValueError("sector provider returned no ranking rows")

    current_snapshot = trade_date is None or target_date == date.today()
    sector_type = "industry"
    matched_sectors: list[SectorSpotRow] = []
    selected: SectorSpotRow | None = None
    selected_history_collector = history_collector
    group_label: str | None = None
    concept_spot_error: Exception | None = None
    industry_match_error: Exception | None = None
    if sector:
        matched_sectors = _resolve_industry_group(sector, spot_rows)
        if matched_sectors:
            sector_type = "industry_group"
            group_label = _industry_group_label(sector)
        else:
            try:
                selected = _resolve_sector(sector, spot_rows)
            except ValueError as error:
                industry_match_error = error
                try:
                    concept_rows = sorted(
                        concept_spot_collector(),
                        key=lambda item: item.rank,
                    )
                    if not concept_rows:
                        raise ValueError("concept provider returned no ranking rows")
                    selected = _resolve_sector(
                        sector,
                        concept_rows,
                        aliases=_CONCEPT_ALIASES,
                    )
                    spot_rows = concept_rows
                    sector_type = "concept"
                    selected_history_collector = concept_history_collector
                except Exception as concept_error:  # noqa: BLE001
                    concept_spot_error = concept_error

    if sector and selected is None and not matched_sectors:
        return _build_history_only_concept(
            requested_sector=sector,
            target_date=target_date,
            history_collector=concept_history_collector,
            industry_error=industry_match_error,
            concept_spot_error=concept_spot_error,
        )

    history: list[SectorDailyRow] = []
    history_error: Exception | None = None
    if selected is not None:
        try:
            history = [
                item
                for item in selected_history_collector(
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
    group_sources = [item.source for item in matched_sectors]
    sources = list(
        dict.fromkeys(
            [
                selected.source if selected else spot_rows[0].source,
                *group_sources,
                *([latest_history.source] if latest_history else []),
            ]
        )
    )
    if history_error is not None:
        sources.append("sector-history-unavailable")

    return SectorPerformanceFacts(
        requested_sector=sector,
        sector_name=selected.sector_name if selected else group_label,
        sector_type=sector_type,
        trade_date=target_date,
        data_as_of=data_as_of,
        data_fresh=(
            current_snapshot or data_as_of == target_date
        ) if not matched_sectors else current_snapshot,
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
        matched_sectors=[_ranking_item(item) for item in matched_sectors]
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


def _resolve_industry_group(
    query: str,
    rows: list[SectorSpotRow],
) -> list[SectorSpotRow]:
    normalized = _normalize_sector_name(query)
    target_names = _INDUSTRY_GROUP_ALIASES.get(normalized)
    if not target_names:
        return []
    normalized_targets = {_normalize_sector_name(item) for item in target_names}
    return [
        item
        for item in rows
        if _normalize_sector_name(item.sector_name) in normalized_targets
    ]


def _industry_group_label(query: str) -> str:
    normalized = _normalize_sector_name(query)
    return _INDUSTRY_GROUP_LABELS.get(normalized, query.strip())


def _resolve_sector(
    query: str,
    rows: list[SectorSpotRow],
    *,
    aliases: dict[str, str] | None = None,
) -> SectorSpotRow:
    normalized = _normalize_sector_name(query)
    if aliases:
        normalized = _normalize_sector_name(aliases.get(normalized, normalized))
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


def _build_history_only_concept(
    *,
    requested_sector: str,
    target_date: date,
    history_collector: HistoryCollector,
    industry_error: Exception | None,
    concept_spot_error: Exception | None,
) -> SectorPerformanceFacts:
    """Keep a named concept query usable when its spot-ranking source is down."""

    normalized = _normalize_sector_name(requested_sector)
    concept_name = _CONCEPT_ALIASES.get(normalized, requested_sector.strip())
    try:
        history = [
            item
            for item in history_collector(
                concept_name,
                target_date - timedelta(days=50),
                target_date,
            )
            if item.trade_date <= target_date
        ]
    except Exception as history_error:  # noqa: BLE001
        details = "; ".join(
            str(item)
            for item in (industry_error, concept_spot_error, history_error)
            if item is not None
        )
        raise ValueError(
            f"未找到与“{requested_sector}”匹配的行业或概念板块"
            + (f"：{details}" if details else "")
        ) from history_error
    if not history:
        raise ValueError(
            f"未找到与“{requested_sector}”匹配的行业或概念板块："
            f"{concept_name}没有可用历史行情"
        )

    latest = history[-1]
    sources = list(
        dict.fromkeys(
            [
                latest.source,
                *(["concept-spot-unavailable"] if concept_spot_error else []),
            ]
        )
    )
    return SectorPerformanceFacts(
        requested_sector=requested_sector,
        sector_name=concept_name,
        sector_type="concept",
        trade_date=target_date,
        data_as_of=latest.trade_date,
        data_fresh=latest.trade_date == target_date,
        sector_count=0,
        change_pct=latest.change_pct,
        return_5d_pct=_period_return(history, 5),
        return_20d_pct=_period_return(history, 20),
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
