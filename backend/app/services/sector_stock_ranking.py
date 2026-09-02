"""Rank one Tonghuashun sector's constituents by completed daily trend facts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkIndexCatalogFact,
    HithinkIndexConstituentFact,
)
from app.models import (
    SectorStockRankingFacts,
    SectorStockTrendItem,
    StockKLineFacts,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.stock_kline import build_stock_kline_facts


MAX_ANALYZED_CONSTITUENTS = 120
DEFAULT_WORKERS = 8
TrendFactsBuilder = Callable[..., StockKLineFacts]


def build_sector_stock_ranking(
    *,
    sector: str,
    end_date: date,
    days: int = 20,
    limit: int = 10,
    collector: HithinkFinanceCollector | None = None,
    repository: SQLiteFirstBoardRepository | None = None,
    facts_builder: TrendFactsBuilder = build_stock_kline_facts,
    max_workers: int = DEFAULT_WORKERS,
) -> SectorStockRankingFacts:
    """Resolve a sector, load bounded member K-lines and rank observed trends."""

    requested_sector = sector.strip()
    if not requested_sector:
        raise ValueError("sector is required")
    requested_days = max(5, min(days, 60))
    requested_limit = max(1, min(limit, 20))
    active_collector = collector or HithinkFinanceCollector()
    active_repository = repository or SQLiteFirstBoardRepository()

    catalogs = [
        *active_collector.collect_index_catalog("industry"),
        *active_collector.collect_index_catalog("cn_concept"),
    ]
    selected = _resolve_sector_index(requested_sector, catalogs)
    constituents = _deduplicate_constituents(
        active_collector.collect_index_constituents(selected)
    )
    if not constituents:
        raise ValueError(f"{selected.name}板块未返回成分股")

    analyzed_members = constituents[:MAX_ANALYZED_CONSTITUENTS]
    facts_by_symbol: dict[str, StockKLineFacts] = {}
    failures: list[str] = []

    def load(member: HithinkIndexConstituentFact) -> StockKLineFacts:
        return facts_builder(
            symbol=member.symbol,
            days=requested_days,
            end_date=end_date,
            repository=active_repository,
        )

    worker_count = max(1, min(max_workers, DEFAULT_WORKERS, len(analyzed_members)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(load, member): member
            for member in analyzed_members
        }
        for future in as_completed(futures):
            member = futures[future]
            try:
                facts_by_symbol[member.symbol] = future.result()
            except Exception:  # noqa: BLE001
                failures.append(member.symbol)

    member_by_symbol = {item.symbol: item for item in analyzed_members}
    ranked = sorted(
        (
            (_trend_score(facts), member_by_symbol[symbol], facts)
            for symbol, facts in facts_by_symbol.items()
        ),
        key=lambda item: (
            -item[0],
            -_sort_metric(item[2].return_20d_pct),
            -_sort_metric(item[2].return_5d_pct),
            item[1].symbol,
        ),
    )
    items = [
        SectorStockTrendItem(
            rank=index,
            symbol=member.symbol,
            name=member.name,
            trend_score=score,
            trend=facts.trend,
            data_as_of=facts.data_as_of,
            latest_close=facts.latest_close,
            return_5d_pct=facts.return_5d_pct,
            return_20d_pct=facts.return_20d_pct,
            ma5=facts.ma5,
            ma10=facts.ma10,
            ma20=facts.ma20,
            volume_ratio_5d=facts.volume_ratio_5d,
            max_drawdown_pct=facts.max_drawdown_pct,
        )
        for index, (score, member, facts) in enumerate(
            ranked[:requested_limit],
            start=1,
        )
    ]
    truncated_count = max(0, len(constituents) - len(analyzed_members))
    warnings: list[str] = []
    if failures:
        warnings.append(f"{len(failures)}只成分股缺少可用K线，未进入排名。")
    if truncated_count:
        warnings.append(
            f"板块成分股超过分析上限，另有{truncated_count}只未进入本次比较。"
        )
    actual_dates = [facts.data_as_of for facts in facts_by_symbol.values()]
    data_as_of = min(actual_dates) if actual_dates else end_date
    return SectorStockRankingFacts(
        requested_sector=requested_sector,
        sector_name=selected.name,
        sector_category=(
            "concept" if selected.category == "cn_concept" else "industry"
        ),
        sector_thscode=selected.thscode,
        requested_days=requested_days,
        requested_limit=requested_limit,
        data_as_of=data_as_of,
        member_count=len(constituents),
        analyzed_count=len(facts_by_symbol),
        missing_count=len(failures),
        truncated_count=truncated_count,
        items=items,
        sources=["hithink-finance", "local-first-daily-kline"],
        warnings=warnings,
    )


def _resolve_sector_index(
    query: str,
    catalogs: list[HithinkIndexCatalogFact],
) -> HithinkIndexCatalogFact:
    """Resolve common industry/concept wording to one deterministic THS index."""

    normalized = _normalize_sector_name(query)
    exact = [item for item in catalogs if _normalize_sector_name(item.name) == normalized]
    if exact:
        return min(exact, key=_sector_preference)
    contains = [
        item
        for item in catalogs
        if normalized in _normalize_sector_name(item.name)
        or _normalize_sector_name(item.name) in normalized
    ]
    if not contains:
        raise ValueError(f"未找到与“{query}”匹配的同花顺行业或概念")
    return min(contains, key=_sector_preference)


def _normalize_sector_name(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    for term in ("今天", "今日", "最近", "近期", "a股", "板块", "行业", "概念", "相关"):
        normalized = normalized.replace(term, "")
    return normalized


def _sector_preference(item: HithinkIndexCatalogFact) -> tuple[int, int, str]:
    return (0 if item.category == "industry" else 1, len(item.name), item.thscode)


def _deduplicate_constituents(
    items: list[HithinkIndexConstituentFact],
) -> list[HithinkIndexConstituentFact]:
    return list({item.symbol: item for item in items if item.symbol}.values())


def _trend_score(facts: StockKLineFacts) -> float:
    """Score only observed price-volume structure on a bounded 0-100 scale."""

    trend_bonus = {
        "rising": 12.0,
        "oscillating": 0.0,
        "falling": -12.0,
        "insufficient": -18.0,
    }[facts.trend]
    return_5d = _bounded(facts.return_5d_pct, -15.0, 15.0)
    return_20d = _bounded(facts.return_20d_pct, -25.0, 25.0)
    volume_ratio = facts.volume_ratio_5d
    volume_bonus = (
        _bounded((volume_ratio - 1.0) * 5.0, -5.0, 5.0)
        if volume_ratio is not None
        else 0.0
    )
    drawdown = abs(min(facts.max_drawdown_pct or 0.0, 0.0))
    drawdown_penalty = min(10.0, max(0.0, drawdown - 8.0) * 0.4)
    score = (
        50.0
        + trend_bonus
        + return_5d * 0.8
        + return_20d * 0.6
        + volume_bonus
        - drawdown_penalty
    )
    return round(max(0.0, min(100.0, score)), 2)


def _bounded(value: float | None, lower: float, upper: float) -> float:
    return max(lower, min(upper, value or 0.0))


def _sort_metric(value: float | None) -> float:
    return value if value is not None else -999.0
