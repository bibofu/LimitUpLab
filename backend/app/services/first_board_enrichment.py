"""Build point-in-time enrichment snapshots for first-board rating inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import mean, pstdev
from typing import Callable

from app.collectors import (
    DragonTigerFact,
    PopularityFact,
    collect_dragon_tiger_facts,
    collect_eastmoney_popularity,
    collect_listing_date,
    collect_recent_listing_dates,
    collect_stock_kline,
)
from app.models import (
    FirstBoardEnrichmentSnapshot,
    LimitUpEvent,
    StockDailyBar,
    StockKLineBar,
)
from app.repositories import SQLiteFirstBoardRepository


ENRICHMENT_FEATURE_VERSION = "first-board-enrichment-v1"
MIN_AMOUNT = 100_000_000
KLineCollector = Callable[[str, int, date | None], list[StockKLineBar]]


@dataclass
class EnrichmentRefreshReport:
    """Compact result from one enrichment refresh."""

    trade_date: str
    candidate_count: int = 0
    snapshot_count: int = 0
    technical_ready_count: int = 0
    listing_date_count: int = 0
    dragon_tiger_count: int = 0
    popularity_count: int = 0
    warnings: list[str] = field(default_factory=list)


def refresh_first_board_enrichment_snapshots(
    *,
    events: list[LimitUpEvent],
    trade_date: date,
    repository: SQLiteFirstBoardRepository,
    kline_collector: KLineCollector = collect_stock_kline,
    listing_collector: Callable[[], dict[str, date]] = collect_recent_listing_dates,
    listing_detail_collector: Callable[[str], date | None] = collect_listing_date,
    dragon_tiger_collector: Callable[[date], dict[str, DragonTigerFact]] = collect_dragon_tiger_facts,
    popularity_collector: Callable[[], dict[str, PopularityFact]] = collect_eastmoney_popularity,
) -> EnrichmentRefreshReport:
    """Collect, derive and persist all enrichment inputs for one rating day."""

    report = EnrichmentRefreshReport(trade_date=trade_date.isoformat())
    candidates = _candidate_events(events, trade_date)
    report.candidate_count = len(candidates)
    if not candidates:
        report.warnings.append("No eligible first-board candidates for enrichment.")
        return report

    listing_dates = _collect_optional(
        listing_collector,
        {},
        report.warnings,
        "listing dates",
    )
    dragon_tiger = _collect_optional(
        lambda: dragon_tiger_collector(trade_date),
        {},
        report.warnings,
        "Dragon-Tiger List",
    )
    popularity = _collect_optional(
        popularity_collector,
        {},
        report.warnings,
        "Eastmoney popularity",
    )

    for event in candidates:
        if event.symbol in listing_dates:
            continue
        try:
            value = listing_detail_collector(event.symbol)
            if value is not None:
                listing_dates[event.symbol] = value
        except Exception as error:  # noqa: BLE001
            report.warnings.append(f"{event.symbol} listing date: {error}")

    snapshots: list[FirstBoardEnrichmentSnapshot] = []
    for event in candidates:
        bars = _load_candidate_bars(
            event=event,
            repository=repository,
            collector=kline_collector,
            warnings=report.warnings,
        )
        snapshot = build_enrichment_snapshot(
            event=event,
            events=events,
            bars=bars,
            listing_date=listing_dates.get(event.symbol),
            dragon_tiger=dragon_tiger.get(event.symbol),
            popularity=popularity.get(event.symbol),
            popularity_source_ready=bool(popularity),
        )
        snapshots.append(snapshot)

    repository.upsert_enrichment_snapshots(snapshots)
    report.snapshot_count = len(snapshots)
    report.technical_ready_count = sum(item.kline_bar_count >= 20 for item in snapshots)
    report.listing_date_count = sum(item.listing_date is not None for item in snapshots)
    report.dragon_tiger_count = sum(item.dragon_tiger_on_list for item in snapshots)
    report.popularity_count = sum(item.popularity_rank is not None for item in snapshots)
    return report


def build_enrichment_snapshot(
    *,
    event: LimitUpEvent,
    events: list[LimitUpEvent],
    bars: list[StockDailyBar],
    listing_date: date | None,
    dragon_tiger: DragonTigerFact | None,
    popularity: PopularityFact | None,
    popularity_source_ready: bool,
) -> FirstBoardEnrichmentSnapshot:
    """Derive one candidate's technical, profile and market-context features."""

    technical = _technical_features(bars, event.trade_date)
    market = _market_context(events, event)
    recent = _recent_limit_up_counts(bars, event.symbol)
    estimated_float_market_cap = (
        event.amount / (event.turnover_rate / 100)
        if event.turnover_rate > 0
        else None
    )
    float_market_cap = (
        dragon_tiger.float_market_cap
        if dragon_tiger and dragon_tiger.float_market_cap
        else estimated_float_market_cap
    )
    float_market_cap_source = (
        "eastmoney_dragon_tiger"
        if dragon_tiger and dragon_tiger.float_market_cap
        else "derived_from_amount_and_turnover" if float_market_cap else None
    )
    missing: list[str] = []
    if technical["kline_bar_count"] < 20:
        missing.append("kline_20d")
    if listing_date is None:
        missing.append("listing_date")
    if float_market_cap is None:
        missing.append("float_market_cap")
    if technical["kline_bar_count"] < 61:
        missing.append("limit_up_history_60d")
    if not popularity_source_ready:
        missing.append("eastmoney_popularity")

    now = datetime.now(timezone.utc)
    return FirstBoardEnrichmentSnapshot(
        trade_date=event.trade_date,
        symbol=event.symbol,
        **technical,
        listing_date=listing_date,
        listing_age_days=(event.trade_date - listing_date).days if listing_date else None,
        float_market_cap=round(float_market_cap, 2) if float_market_cap else None,
        float_market_cap_source=float_market_cap_source,
        recent_limit_up_count_20d=recent[0],
        recent_limit_up_count_60d=recent[1],
        **market,
        dragon_tiger_on_list=dragon_tiger is not None,
        dragon_tiger_net_buy_amount=dragon_tiger.net_buy_amount if dragon_tiger else None,
        dragon_tiger_buy_amount=dragon_tiger.buy_amount if dragon_tiger else None,
        dragon_tiger_sell_amount=dragon_tiger.sell_amount if dragon_tiger else None,
        dragon_tiger_reason=dragon_tiger.reason if dragon_tiger else None,
        popularity_rank=popularity.rank if popularity else None,
        popularity_rank_change=popularity.rank_change if popularity else None,
        popularity_snapshot_at=popularity.captured_at if popularity else None,
        data_missing=missing,
        feature_version=ENRICHMENT_FEATURE_VERSION,
        created_at=now,
    )


def _candidate_events(events: list[LimitUpEvent], trade_date: date) -> list[LimitUpEvent]:
    return [
        item
        for item in events
        if item.trade_date == trade_date
        and item.board_height == 1
        and item.closed_limit
        and item.amount >= MIN_AMOUNT
        and "ST" not in item.name.upper()
        and not item.name.startswith(("N", "C"))
        and not item.symbol.startswith(("4", "8", "920", "688", "689"))
    ]


def _load_candidate_bars(
    *,
    event: LimitUpEvent,
    repository: SQLiteFirstBoardRepository,
    collector: KLineCollector,
    warnings: list[str],
) -> list[StockDailyBar]:
    try:
        raw_bars = collector(event.symbol, 65, event.trade_date)
        normalized = [
            StockDailyBar(
                symbol=event.symbol,
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=0,
                change_pct=None,
                source="akshare.stock_zh_a_hist_tx",
                created_at=datetime.now(timezone.utc),
            )
            for bar in raw_bars
            if bar.trade_date <= event.trade_date
        ]
        if normalized:
            repository.upsert_daily_bars(normalized)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"{event.symbol} 60-day K-line: {error}")
    return [
        bar
        for bar in repository.list_daily_bars(event.symbol)
        if bar.trade_date <= event.trade_date
    ][-65:]


def _technical_features(bars: list[StockDailyBar], trade_date: date) -> dict[str, object]:
    ordered = sorted((bar for bar in bars if bar.trade_date <= trade_date), key=lambda item: item.trade_date)
    closes = [item.close for item in ordered]
    volumes = [item.volume for item in ordered]
    current_close = closes[-1] if closes else None
    ma5 = mean(closes[-5:]) if len(closes) >= 5 else None
    ma10 = mean(closes[-10:]) if len(closes) >= 10 else None
    ma20 = mean(closes[-20:]) if len(closes) >= 20 else None
    if ma5 is not None and ma10 is not None and ma20 is not None:
        if ma5 > ma10 > ma20:
            alignment = "bullish"
        elif ma5 < ma10 < ma20:
            alignment = "bearish"
        else:
            alignment = "mixed"
    else:
        alignment = "unknown"

    daily_returns = [
        ((closes[index] - closes[index - 1]) / closes[index - 1]) * 100
        for index in range(max(1, len(closes) - 20), len(closes))
        if closes[index - 1]
    ]
    previous_five_volumes = volumes[-6:-1]
    return {
        "kline_bar_count": len(ordered),
        "return_5d_pct": _period_return(closes, 5),
        "return_20d_pct": _period_return(closes, 20),
        "return_60d_pct": _period_return(closes, 60),
        "distance_20d_high_pct": _distance_from_high(ordered[-20:]),
        "distance_60d_high_pct": _distance_from_high(ordered[-60:]),
        "volume_ratio_5d": (
            round(volumes[-1] / mean(previous_five_volumes), 3)
            if volumes and previous_five_volumes and mean(previous_five_volumes) > 0
            else None
        ),
        "volatility_20d": round(pstdev(daily_returns), 3) if len(daily_returns) >= 2 else None,
        "close_above_ma20": current_close > ma20 if current_close is not None and ma20 is not None else None,
        "ma_alignment": alignment,
    }


def _period_return(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or not closes[-days - 1]:
        return None
    return round(((closes[-1] - closes[-days - 1]) / closes[-days - 1]) * 100, 3)


def _distance_from_high(bars: list[StockDailyBar]) -> float | None:
    if not bars:
        return None
    highest = max(item.high for item in bars)
    return round(((bars[-1].close - highest) / highest) * 100, 3) if highest else None


def _recent_limit_up_counts(
    bars: list[StockDailyBar],
    symbol: str,
) -> tuple[int, int]:
    """Count probable limit-up closes from daily bars using board-specific limits."""

    ordered = sorted(bars, key=lambda item: item.trade_date)
    threshold = 19.5 if symbol.startswith(("300", "301")) else 9.5
    daily_returns = [
        ((ordered[index].close - ordered[index - 1].close) / ordered[index - 1].close) * 100
        for index in range(1, len(ordered))
        if ordered[index - 1].close
    ]
    return (
        sum(value >= threshold for value in daily_returns[-20:]),
        sum(value >= threshold for value in daily_returns[-60:]),
    )


def _market_context(events: list[LimitUpEvent], target: LimitUpEvent) -> dict[str, object]:
    same_day = [item for item in events if item.trade_date == target.trade_date]
    industry = [item for item in same_day if item.industry == target.industry]
    industry_first = [item for item in industry if item.board_height == 1 and item.closed_limit]
    industry_continued = [item for item in industry if item.board_height >= 2 and item.closed_limit]
    industry_failed = [item for item in industry if not item.closed_limit]
    ordered_first = sorted(industry_first, key=lambda item: (item.first_limit_time, item.symbol))

    available_dates = sorted({item.trade_date for item in events if item.trade_date <= target.trade_date})
    previous_date = available_dates[-2] if len(available_dates) >= 2 else None
    previous_first = [
        item
        for item in events
        if previous_date is not None
        and item.trade_date == previous_date
        and item.board_height == 1
        and item.closed_limit
    ]
    current_promoted_symbols = {
        item.symbol
        for item in same_day
        if item.closed_limit and item.board_height >= 2
    }
    first_attempts = [item for item in same_day if item.board_height == 1]
    return {
        "industry_first_board_count": len(industry_first),
        "industry_continued_board_count": len(industry_continued),
        "industry_failed_count": len(industry_failed),
        "industry_max_board_height": max((item.board_height for item in industry if item.closed_limit), default=0),
        "industry_first_limit_rank": (
            next(
                (index for index, item in enumerate(ordered_first, start=1) if item.symbol == target.symbol),
                None,
            )
        ),
        "previous_first_board_promotion_rate": (
            sum(item.symbol in current_promoted_symbols for item in previous_first) / len(previous_first)
            if previous_first
            else None
        ),
        "market_first_board_seal_rate": (
            sum(item.closed_limit for item in first_attempts) / len(first_attempts)
            if first_attempts
            else None
        ),
    }


def _collect_optional(
    collector: Callable[[], object],
    fallback: object,
    warnings: list[str],
    label: str,
):
    try:
        return collector()
    except Exception as error:  # noqa: BLE001
        warnings.append(f"{label}: {error}")
        return fallback
