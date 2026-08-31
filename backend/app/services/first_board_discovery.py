"""Deterministic two-stage discovery for next-session first-board candidates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from math import log10
from statistics import mean, pstdev
from typing import Callable

from app.collectors import (
    HithinkFinanceCollector,
    collect_a_share_trade_dates,
    collect_stock_kline,
)
from app.collectors.hithink_finance_collector import (
    HithinkMarketSnapshot,
    HithinkMarketSnapshotFact,
    SHANGHAI_TIMEZONE,
)
from app.models import (
    FirstBoardDiscoveryCandidate,
    FirstBoardDiscoveryFacts,
    FirstBoardDiscoveryResponse,
    ScoreBreakdownItem,
    StockDailyBar,
    StockKLineBar,
)
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
)


FIRST_BOARD_DISCOVERY_VERSION = "first-board-discovery-v1-price-volume"
MIN_DISCOVERY_AMOUNT = 100_000_000
MIN_HISTORY_BARS = 40
DEFAULT_RECALL_LIMIT = 60
DEFAULT_TOP_K = 10
DEFAULT_HISTORY_WORKERS = 8

MarketCollector = Callable[[], HithinkMarketSnapshot]
HistoryCollector = Callable[[str, int, date | None], list[StockKLineBar]]
CalendarCollector = Callable[[date, date], list[date]]


def refresh_first_board_discovery(
    *,
    target_trade_date: date | None = None,
    recall_limit: int = DEFAULT_RECALL_LIMIT,
    top_k: int = DEFAULT_TOP_K,
    max_workers: int = DEFAULT_HISTORY_WORKERS,
    market_collector: MarketCollector | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    calendar_collector: CalendarCollector = collect_a_share_trade_dates,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    snapshot_repository: SQLiteFirstBoardDiscoveryRepository | None = None,
    force: bool = False,
) -> FirstBoardDiscoveryResponse:
    """Collect the market, rank candidates and persist one immutable snapshot."""

    active_market_collector = market_collector or (
        HithinkFinanceCollector().collect_full_market_snapshot
    )
    market_snapshot = active_market_collector()
    data_as_of = market_snapshot.captured_at.astimezone(SHANGHAI_TIMEZONE).date()
    calendar_warning: str | None = None
    if target_trade_date is None:
        try:
            future_dates = calendar_collector(
                data_as_of + timedelta(days=1),
                data_as_of + timedelta(days=14),
            )
            target_trade_date = future_dates[0] if future_dates else None
        except Exception as error:  # noqa: BLE001
            calendar_warning = f"下一交易日解析失败：{error}"
    eligible = [
        item for item in market_snapshot.items if _eligible_snapshot(item)
    ]
    recalled = sorted(
        eligible,
        key=lambda item: (-_recall_score(item), item.symbol),
    )[: max(1, min(recall_limit, 200))]
    histories, collection_errors = _collect_histories(
        recalled,
        data_as_of=data_as_of,
        history_collector=history_collector,
        max_workers=max_workers,
    )
    active_first_board_repository = (
        first_board_repository or SQLiteFirstBoardRepository()
    )
    _persist_histories(
        histories,
        recalled,
        repository=active_first_board_repository,
        data_as_of=data_as_of,
    )

    candidates: list[FirstBoardDiscoveryCandidate] = []
    insufficient_history_count = 0
    for item in recalled:
        bars = _merge_snapshot_bar(histories.get(item.symbol, []), item, data_as_of)
        if len(bars) < MIN_HISTORY_BARS:
            insufficient_history_count += 1
            continue
        candidates.append(
            _build_candidate(
                item,
                bars,
                data_as_of=data_as_of,
                target_trade_date=target_trade_date,
            )
        )
    candidates.sort(key=lambda item: (-item.score, -item.confidence, item.facts.symbol))

    warnings = [
        "首板挖掘 v1 是量价结构基线，尚未把板块强度、公告和新闻催化纳入分数。",
        "评分用于收盘后研究排序，不代表涨停概率，也不构成交易建议。",
    ]
    if calendar_warning:
        warnings.append(calendar_warning)
    if collection_errors:
        warnings.append(f"{collection_errors} 只召回股票的历史 K 线获取失败，已排除。")
    if insufficient_history_count:
        warnings.append(
            f"{insufficient_history_count} 只股票历史不足 {MIN_HISTORY_BARS} 根，"
            "按次新或数据不足排除。"
        )
    response = FirstBoardDiscoveryResponse(
        data_as_of=data_as_of,
        target_trade_date=target_trade_date,
        universe_count=market_snapshot.total or len(market_snapshot.items),
        eligible_count=len(eligible),
        recalled_count=len(recalled),
        candidates=candidates[: max(1, min(top_k, 30))],
        generated_by=FIRST_BOARD_DISCOVERY_VERSION,
        source=market_snapshot.source,
        snapshot_created_at=datetime.now(timezone.utc),
        warnings=warnings,
    )
    active_snapshot_repository = snapshot_repository or (
        SQLiteFirstBoardDiscoveryRepository(active_first_board_repository.database_path)
    )
    active_snapshot_repository.save(response, replace=force)
    return response


def _collect_histories(
    items: list[HithinkMarketSnapshotFact],
    *,
    data_as_of: date,
    history_collector: HistoryCollector,
    max_workers: int,
) -> tuple[dict[str, list[StockKLineBar]], int]:
    histories: dict[str, list[StockKLineBar]] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 16))) as executor:
        futures = {
            executor.submit(history_collector, item.symbol, 65, data_as_of): item.symbol
            for item in items
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception:  # noqa: BLE001
                errors += 1
    return histories, errors


def _persist_histories(
    histories: dict[str, list[StockKLineBar]],
    items: list[HithinkMarketSnapshotFact],
    *,
    repository: SQLiteFirstBoardRepository,
    data_as_of: date,
) -> None:
    snapshot_by_symbol = {item.symbol: item for item in items}
    created_at = datetime.now(timezone.utc)
    rows: list[StockDailyBar] = []
    for symbol, bars in histories.items():
        snapshot = snapshot_by_symbol[symbol]
        for bar in _merge_snapshot_bar(bars, snapshot, data_as_of):
            rows.append(
                StockDailyBar(
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    amount=(snapshot.turnover or 0)
                    if bar.trade_date == data_as_of
                    else 0,
                    change_pct=snapshot.change_pct if bar.trade_date == data_as_of else None,
                    source="first-board-discovery",
                    created_at=created_at,
                )
            )
    if rows:
        repository.upsert_daily_bars(rows)


def _eligible_snapshot(item: HithinkMarketSnapshotFact) -> bool:
    required = (
        item.last_price,
        item.change_pct,
        item.turnover,
        item.volume,
        item.open_price,
        item.high_price,
        item.low_price,
        item.previous_close,
    )
    if any(value is None for value in required):
        return False
    if not _supported_symbol(item.symbol) or _risk_warning_name(item.name):
        return False
    if min(
        item.last_price or 0,
        item.open_price or 0,
        item.high_price or 0,
        item.low_price or 0,
        item.previous_close or 0,
    ) <= 0:
        return False
    if (item.turnover or 0) < MIN_DISCOVERY_AMOUNT or (item.volume or 0) <= 0:
        return False
    change_pct = item.change_pct or 0
    if change_pct < -5:
        return False
    return change_pct < (19.5 if item.symbol.startswith(("300", "301")) else 9.5)


def _supported_symbol(symbol: str) -> bool:
    if not (len(symbol) == 6 and symbol.isdigit()):
        return False
    if symbol.startswith(("4", "8", "920", "688", "689")):
        return False
    return symbol.startswith(("0", "3", "6"))


def _risk_warning_name(name: str) -> bool:
    normalized = name.upper().replace("*", "")
    return "ST" in normalized or "退" in name or name.startswith(("N", "C"))


def _recall_score(item: HithinkMarketSnapshotFact) -> float:
    change_pct = item.change_pct or 0
    momentum = max(0.0, 24 - abs(change_pct - 4.0) * 3.0)
    location = _close_location(item) * 22
    amount = min(20.0, max(0.0, (log10(max(item.turnover or 1, 1)) - 8) * 10))
    range_pct = _intraday_range_pct(item)
    range_score = max(0.0, 18 - abs(range_pct - 5.0) * 2.0)
    open_to_close = _open_to_close_pct(item)
    body_score = min(16.0, max(0.0, 8 + open_to_close * 2.0))
    return momentum + location + amount + range_score + body_score


def _build_candidate(
    item: HithinkMarketSnapshotFact,
    bars: list[StockKLineBar],
    *,
    data_as_of: date,
    target_trade_date: date | None,
) -> FirstBoardDiscoveryCandidate:
    return_5d = _period_return(bars, 5)
    return_20d = _period_return(bars, 20)
    volume_ratio = _volume_ratio(bars)
    distance_high = _distance_to_high(bars, 20)
    volatility = _volatility(bars, 20)
    ma_alignment = _ma_alignment(bars)
    pattern = _classify_pattern(
        return_5d=return_5d,
        return_20d=return_20d,
        distance_high=distance_high,
        volume_ratio=volume_ratio,
        ma_alignment=ma_alignment,
    )
    missing = ["sector_strength", "news_catalyst"]
    facts = FirstBoardDiscoveryFacts(
        symbol=item.symbol,
        name=item.name or item.symbol,
        data_as_of=data_as_of,
        target_trade_date=target_trade_date,
        close=item.last_price or bars[-1].close,
        change_pct=item.change_pct or 0,
        amount=item.turnover or 0,
        volume=item.volume or bars[-1].volume,
        intraday_range_pct=_intraday_range_pct(item),
        close_location=_close_location(item),
        open_to_close_pct=_open_to_close_pct(item),
        kline_bar_count=len(bars),
        return_5d_pct=return_5d,
        return_20d_pct=return_20d,
        distance_20d_high_pct=distance_high,
        volume_ratio_5d=volume_ratio,
        volatility_20d=volatility,
        ma_alignment=ma_alignment,
        pattern=pattern,
        data_missing=missing,
    )
    breakdown = _score_breakdown(facts)
    score = round(sum(item.score for item in breakdown), 1)
    confidence = round(min(0.82, 0.52 + min(len(bars), 60) / 300), 2)
    ordered = sorted(
        [value for value in breakdown if value.name != "数据完整性"],
        key=lambda value: -value.score,
    )
    reasons = [value.evidence[0] for value in ordered[:3]]
    risks = ["板块强度和事件催化尚未进入 v1 分数"]
    if volume_ratio is not None and volume_ratio > 4:
        risks.append("量比过高，需警惕单日资金透支")
    if (item.change_pct or 0) > 7:
        risks.append("当日涨幅较高，次日承接不确定性较大")
    if volatility is not None and volatility > 4.5:
        risks.append("近 20 日波动率偏高")
    return FirstBoardDiscoveryCandidate(
        facts=facts,
        score=score,
        rating=_rating(score),
        confidence=confidence,
        score_breakdown=breakdown,
        reasons=reasons,
        risks=risks,
    )


def _score_breakdown(facts: FirstBoardDiscoveryFacts) -> list[ScoreBreakdownItem]:
    momentum = _bounded(20 - abs(facts.change_pct - 4) * 1.8, 0, 20)
    if facts.return_5d_pct is not None:
        momentum = (momentum + _bounded(20 - abs(facts.return_5d_pct - 7), 0, 20)) / 2
    close_strength = _bounded(facts.close_location * 15, 0, 15)
    ratio = facts.volume_ratio_5d or 0
    volume_expansion = (
        _bounded(20 - abs(ratio - 2) * 8, 0, 20) if ratio > 0 else 0
    )
    distance = facts.distance_20d_high_pct
    breakout = _bounded(20 - abs(distance or -20) * 1.5, 0, 20)
    if facts.ma_alignment == "bullish":
        breakout = min(20, breakout + 3)
    liquidity = _bounded(
        10 - abs(log10(max(facts.amount, 1)) - 9.0) * 3,
        0,
        10,
    )
    intraday = _bounded(
        10 - abs(facts.intraday_range_pct - 5) * 1.1
        + max(0, facts.open_to_close_pct),
        0,
        10,
    )
    data_quality = 5 if facts.kline_bar_count >= 60 else 3
    return [
        ScoreBreakdownItem(
            name="短期动量",
            score=round(momentum, 2),
            max_score=20,
            evidence=[
                f"当日涨幅 {facts.change_pct:+.1f}%，近 5 日 {facts.return_5d_pct or 0:+.1f}%"
            ],
        ),
        ScoreBreakdownItem(
            name="收盘强度",
            score=round(close_strength, 2),
            max_score=15,
            evidence=[f"收盘位于日内区间 {facts.close_location:.0%} 位置"],
        ),
        ScoreBreakdownItem(
            name="量能扩张",
            score=round(volume_expansion, 2),
            max_score=20,
            evidence=[f"近 5 日量比 {facts.volume_ratio_5d or 0:.2f}"],
        ),
        ScoreBreakdownItem(
            name="突破位置",
            score=round(breakout, 2),
            max_score=20,
            evidence=[
                f"距 20 日高点 {facts.distance_20d_high_pct or 0:+.1f}%，"
                f"均线结构{_ma_alignment_label(facts.ma_alignment)}"
            ],
        ),
        ScoreBreakdownItem(
            name="流动性",
            score=round(liquidity, 2),
            max_score=10,
            evidence=[f"成交额 {facts.amount / 100_000_000:.1f} 亿元"],
        ),
        ScoreBreakdownItem(
            name="日内质量",
            score=round(intraday, 2),
            max_score=10,
            evidence=[
                f"振幅 {facts.intraday_range_pct:.1f}%，开盘至收盘 {facts.open_to_close_pct:+.1f}%"
            ],
        ),
        ScoreBreakdownItem(
            name="数据完整性",
            score=data_quality,
            max_score=5,
            evidence=[f"可用日 K {facts.kline_bar_count} 根"],
        ),
    ]


def _merge_snapshot_bar(
    bars: list[StockKLineBar],
    item: HithinkMarketSnapshotFact,
    data_as_of: date,
) -> list[StockKLineBar]:
    existing_by_date = {
        bar.trade_date: bar for bar in bars if bar.trade_date <= data_as_of
    }
    existing_current = existing_by_date.get(data_as_of)
    current = StockKLineBar(
        trade_date=data_as_of,
        open=item.open_price or item.last_price or 0,
        high=item.high_price or item.last_price or 0,
        low=item.low_price or item.last_price or 0,
        close=item.last_price or 0,
        volume=(
            existing_current.volume
            if existing_current is not None and existing_current.volume > 0
            else _normalize_snapshot_volume(item.volume or 0, list(existing_by_date.values()))
        ),
    )
    existing_by_date[data_as_of] = current
    return [existing_by_date[value] for value in sorted(existing_by_date)][-65:]


def _normalize_snapshot_volume(
    snapshot_volume: float,
    historical_bars: list[StockKLineBar],
) -> float:
    """Align a share-based quote volume with K-line lots when today's bar is absent."""

    recent = [bar.volume for bar in historical_bars[-5:] if bar.volume > 0]
    if not recent or snapshot_volume <= 0:
        return snapshot_volume
    baseline = mean(recent)
    if snapshot_volume / baseline >= 20:
        return snapshot_volume / 100
    return snapshot_volume


def _close_location(item: HithinkMarketSnapshotFact) -> float:
    high = item.high_price or 0
    low = item.low_price or 0
    if high <= low:
        return 0.5
    return _bounded(((item.last_price or low) - low) / (high - low), 0, 1)


def _intraday_range_pct(item: HithinkMarketSnapshotFact) -> float:
    previous = item.previous_close or 0
    return (
        round(((item.high_price or 0) - (item.low_price or 0)) / previous * 100, 3)
        if previous > 0
        else 0
    )


def _open_to_close_pct(item: HithinkMarketSnapshotFact) -> float:
    opening = item.open_price or 0
    return (
        round(((item.last_price or 0) / opening - 1) * 100, 3)
        if opening > 0
        else 0
    )


def _period_return(bars: list[StockKLineBar], periods: int) -> float | None:
    if len(bars) <= periods or bars[-periods - 1].close <= 0:
        return None
    return round((bars[-1].close / bars[-periods - 1].close - 1) * 100, 3)


def _volume_ratio(bars: list[StockKLineBar]) -> float | None:
    if len(bars) < 6:
        return None
    baseline = mean(item.volume for item in bars[-6:-1])
    return round(bars[-1].volume / baseline, 3) if baseline > 0 else None


def _distance_to_high(bars: list[StockKLineBar], periods: int) -> float | None:
    if not bars:
        return None
    high = max(item.high for item in bars[-periods:])
    return round((bars[-1].close / high - 1) * 100, 3) if high > 0 else None


def _volatility(bars: list[StockKLineBar], periods: int) -> float | None:
    window = bars[-(periods + 1):]
    returns = [
        (current.close / previous.close - 1) * 100
        for previous, current in zip(window, window[1:])
        if previous.close > 0
    ]
    return round(pstdev(returns), 3) if len(returns) >= 5 else None


def _ma_alignment(bars: list[StockKLineBar]) -> str:
    if len(bars) < 20:
        return "insufficient"
    ma5 = mean(item.close for item in bars[-5:])
    ma10 = mean(item.close for item in bars[-10:])
    ma20 = mean(item.close for item in bars[-20:])
    if bars[-1].close >= ma5 >= ma10 >= ma20:
        return "bullish"
    if bars[-1].close <= ma5 <= ma10 <= ma20:
        return "bearish"
    return "mixed"


def _ma_alignment_label(value: str) -> str:
    return {
        "bullish": "多头排列",
        "bearish": "空头排列",
        "mixed": "交织",
        "insufficient": "数据不足",
    }.get(value, "未分类")


def _classify_pattern(
    *,
    return_5d: float | None,
    return_20d: float | None,
    distance_high: float | None,
    volume_ratio: float | None,
    ma_alignment: str,
) -> str:
    r5 = return_5d or 0
    r20 = return_20d or 0
    distance = distance_high if distance_high is not None else -100
    ratio = volume_ratio or 0
    if r20 <= -12 and r5 >= 2:
        return "oversold_rebound"
    if r20 >= 15 and -8 <= r5 <= 8 and distance >= -5:
        return "second_wave"
    if ma_alignment == "bullish" and r5 >= 4:
        return "trend_acceleration"
    if -8 <= r20 <= 15 and distance >= -3 and ratio >= 1.2:
        return "low_base_breakout"
    if distance >= -3:
        return "range_breakout"
    return "unclassified"


def _rating(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _bounded(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
