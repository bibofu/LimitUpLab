"""Deterministic two-stage discovery for next-session first-board candidates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import log10
import re
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
    HithinkIndexSnapshotFact,
    SHANGHAI_TIMEZONE,
)
from app.models import (
    FirstBoardDiscoveryCandidate,
    FirstBoardDiscoveryFacts,
    FirstBoardDiscoveryResponse,
    FirstBoardDiscoveryTheme,
    ScoreBreakdownItem,
    StockDailyBar,
    StockKLineBar,
)
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
)
from app.services.finance_news import collect_finance_news


FIRST_BOARD_DISCOVERY_VERSION = "first-board-discovery-v2-theme-driven"
MIN_DISCOVERY_AMOUNT = 100_000_000
MIN_HISTORY_BARS = 40
DEFAULT_RECALL_LIMIT = 60
DEFAULT_TOP_K = 10
DEFAULT_HISTORY_WORKERS = 8
MAX_DISCOVERY_THEMES = 10

MarketCollector = Callable[[], HithinkMarketSnapshot]
HistoryCollector = Callable[[str, int, date | None], list[StockKLineBar]]
CalendarCollector = Callable[[date, date], list[date]]


@dataclass(frozen=True)
class FirstBoardDiscoveryContext:
    """Hot-theme membership, news and popularity facts captured before scoring."""

    themes: list[FirstBoardDiscoveryTheme]
    memberships: dict[str, list[FirstBoardDiscoveryTheme]]
    popularity_ranks: dict[str, int]
    warnings: list[str]


ThemeCollector = Callable[[date], FirstBoardDiscoveryContext]


def _collect_hot_theme_context(
    collector: HithinkFinanceCollector,
    data_as_of: date,
) -> FirstBoardDiscoveryContext:
    """Resolve hot indexes to constituents and attach time-bounded news evidence."""

    del data_as_of
    warnings: list[str] = []
    catalogs = [
        *collector.collect_index_catalog("cn_concept"),
        *collector.collect_index_catalog("industry"),
    ]
    snapshots = collector.collect_index_snapshots(catalogs)
    try:
        news = collect_finance_news(limit=12, hours=48)
        news_items = news.items
    except Exception as error:  # noqa: BLE001
        news_items = []
        warnings.append(f"财经快讯不可用，新闻催化未计分：{error}")

    news_by_theme = {
        item.thscode: [
            news_item.title
            for news_item in news_items
            if _theme_matches_news(
                item.name,
                f"{news_item.title} {news_item.summary}",
            )
        ][:3]
        for item in snapshots
    }
    selected = _select_hot_theme_snapshots(snapshots, news_by_theme)
    memberships: dict[str, list[FirstBoardDiscoveryTheme]] = {}
    themes: list[FirstBoardDiscoveryTheme] = []
    for index_snapshot, rank in selected:
        try:
            constituents = collector.collect_index_constituents(index_snapshot)
        except Exception as error:  # noqa: BLE001
            warnings.append(f"{index_snapshot.name}成分股获取失败：{error}")
            continue
        theme = FirstBoardDiscoveryTheme(
            name=index_snapshot.name,
            category=(
                "concept" if index_snapshot.category == "cn_concept" else "industry"
            ),
            change_pct=round(index_snapshot.change_pct or 0, 2),
            rank=rank,
            member_count=len(constituents),
            news_headlines=news_by_theme.get(index_snapshot.thscode, []),
        )
        themes.append(theme)
        for constituent in constituents:
            memberships.setdefault(constituent.symbol, []).append(theme)

    try:
        popularity = collector.collect_hot_stocks(period="day", limit=100)
        popularity_ranks = {item.symbol: item.rank for item in popularity.items}
    except Exception as error:  # noqa: BLE001
        popularity_ranks = {}
        warnings.append(f"热股榜不可用，个股关注度未计分：{error}")
    return FirstBoardDiscoveryContext(
        themes=themes,
        memberships=memberships,
        popularity_ranks=popularity_ranks,
        warnings=warnings,
    )


def _select_hot_theme_snapshots(
    snapshots: list[HithinkIndexSnapshotFact],
    news_by_theme: dict[str, list[str]],
) -> list[tuple[HithinkIndexSnapshotFact, int]]:
    """Select strong market themes plus a small number of news-confirmed themes."""

    ranked_by_category: dict[str, list[HithinkIndexSnapshotFact]] = {}
    rank_lookup: dict[str, int] = {}
    for category in ("cn_concept", "industry"):
        ranked = sorted(
            [
                item
                for item in snapshots
                if item.category == category and item.change_pct is not None
            ],
            key=lambda item: (-(item.change_pct or 0), item.name),
        )
        ranked_by_category[category] = ranked
        rank_lookup.update({item.thscode: index + 1 for index, item in enumerate(ranked)})

    selected = [
        *[
            item
            for item in ranked_by_category.get("cn_concept", [])
            if (item.change_pct or 0) >= 1
        ][:5],
        *[
            item
            for item in ranked_by_category.get("industry", [])
            if (item.change_pct or 0) >= 1
        ][:3],
    ]
    news_confirmed = sorted(
        [
            item
            for item in snapshots
            if news_by_theme.get(item.thscode) and (item.change_pct or 0) > 0
        ],
        key=lambda item: (
            -len(news_by_theme.get(item.thscode, [])),
            -(item.change_pct or 0),
            item.name,
        ),
    )
    selected.extend(news_confirmed[:2])
    unique = list({item.thscode: item for item in selected}.values())[
        :MAX_DISCOVERY_THEMES
    ]
    return [(item, rank_lookup.get(item.thscode, 999)) for item in unique]


def _theme_matches_news(theme_name: str, news_text: str) -> bool:
    normalized_name = theme_name.lower().replace("概念", "").replace("行业", "")
    normalized_text = " ".join(news_text.lower().split())
    compact_text = normalized_text.replace(" ", "")
    if len(normalized_name) >= 2 and normalized_name in compact_text:
        return True
    aliases = (
        (("ai", "人工智能"), ("ai", "人工智能")),
        (("短剧", "影视", "传媒", "视频"), ("短剧", "影视", "传媒", "视频")),
        (("芯片", "半导体"), ("芯片", "半导体")),
        (("机器人",), ("机器人", "人形")),
        (("算力", "数据中心"), ("算力", "数据中心", "液冷")),
        (("种业", "农业"), ("种业", "农业", "粮食")),
        (("医药", "创新药"), ("医药", "创新药")),
        (("军工", "大飞机"), ("军工", "大飞机", "航空航天")),
    )
    return any(
        any(term in normalized_name for term in theme_terms)
        and any(
            _news_term_matches(
                normalized_text=normalized_text,
                compact_text=compact_text,
                term=term,
            )
            for term in news_terms
        )
        for theme_terms, news_terms in aliases
    )


def _news_term_matches(
    *,
    normalized_text: str,
    compact_text: str,
    term: str,
) -> bool:
    """Match ASCII aliases as tokens so words such as chairman do not imply AI."""

    if term.isascii() and term.isalnum():
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                normalized_text,
            )
        )
    return term in compact_text


def refresh_first_board_discovery(
    *,
    target_trade_date: date | None = None,
    recall_limit: int = DEFAULT_RECALL_LIMIT,
    top_k: int = DEFAULT_TOP_K,
    max_workers: int = DEFAULT_HISTORY_WORKERS,
    market_collector: MarketCollector | None = None,
    theme_collector: ThemeCollector | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    calendar_collector: CalendarCollector = collect_a_share_trade_dates,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    snapshot_repository: SQLiteFirstBoardDiscoveryRepository | None = None,
    force: bool = False,
) -> FirstBoardDiscoveryResponse:
    """Build a theme-led candidate universe, then rank it with K-line facts."""

    hithink_collector = HithinkFinanceCollector()
    active_market_collector = market_collector or hithink_collector.collect_full_market_snapshot
    market_snapshot = active_market_collector()
    data_as_of = market_snapshot.captured_at.astimezone(SHANGHAI_TIMEZONE).date()
    active_theme_collector = theme_collector or (
        lambda target: _collect_hot_theme_context(hithink_collector, target)
    )
    try:
        discovery_context = active_theme_collector(data_as_of)
    except Exception as error:  # noqa: BLE001
        discovery_context = FirstBoardDiscoveryContext(
            themes=[],
            memberships={},
            popularity_ranks={},
            warnings=[f"热门题材数据获取失败：{error}"],
        )
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
        item
        for item in market_snapshot.items
        if _eligible_snapshot(item) and item.symbol in discovery_context.memberships
    ]
    recalled = sorted(
        eligible,
        key=lambda item: (
            -_recall_score(item, discovery_context),
            item.symbol,
        ),
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
                discovery_context=discovery_context,
            )
        )
    candidates.sort(key=lambda item: (-item.score, -item.confidence, item.facts.symbol))

    warnings = [
        "首板挖掘先按热门题材构建候选池，再使用量价和位置结构精排。",
        "评分是题材与量价研究排序，不代表涨停概率，也不构成交易建议。",
        *discovery_context.warnings,
    ]
    if not discovery_context.themes:
        warnings.append("未获得可用热门题材，本期不使用全市场量价候选补位。")
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
        themes=discovery_context.themes,
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


def _recall_score(
    item: HithinkMarketSnapshotFact,
    discovery_context: FirstBoardDiscoveryContext,
) -> float:
    themes = discovery_context.memberships.get(item.symbol, [])
    theme_priority = _theme_priority(themes) * 1.8
    popularity_rank = discovery_context.popularity_ranks.get(item.symbol)
    popularity = (
        max(0.0, 16 - (popularity_rank - 1) * 0.15)
        if popularity_rank is not None
        else 0
    )
    change_pct = item.change_pct or 0
    momentum = max(0.0, 24 - abs(change_pct - 4.0) * 3.0)
    location = _close_location(item) * 22
    amount = min(20.0, max(0.0, (log10(max(item.turnover or 1, 1)) - 8) * 10))
    range_pct = _intraday_range_pct(item)
    range_score = max(0.0, 18 - abs(range_pct - 5.0) * 2.0)
    open_to_close = _open_to_close_pct(item)
    body_score = min(16.0, max(0.0, 8 + open_to_close * 2.0))
    return theme_priority + popularity + momentum + location + amount + range_score + body_score


def _build_candidate(
    item: HithinkMarketSnapshotFact,
    bars: list[StockKLineBar],
    *,
    data_as_of: date,
    target_trade_date: date | None,
    discovery_context: FirstBoardDiscoveryContext,
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
    themes = discovery_context.memberships.get(item.symbol, [])
    news_catalysts = list(
        dict.fromkeys(
            headline
            for theme in themes
            for headline in theme.news_headlines
        )
    )[:3]
    popularity_rank = discovery_context.popularity_ranks.get(item.symbol)
    missing = []
    if not news_catalysts:
        missing.append("news_catalyst")
    if popularity_rank is None:
        missing.append("popularity_rank")
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
        themes=themes,
        popularity_rank=popularity_rank,
        news_catalysts=news_catalysts,
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
    risks = []
    if not news_catalysts:
        risks.append("暂未匹配到明确新闻催化，当前主要由题材强度驱动")
    if popularity_rank is None:
        risks.append("未进入热股 Top100，个股关注度尚未形成共振")
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
    theme_strength = _bounded(_theme_priority(facts.themes), 0, 30)
    catalyst = _bounded(len(facts.news_catalysts) * 7.5, 0, 15)
    popularity = (
        _bounded(10 - (facts.popularity_rank - 1) * 0.1, 1, 10)
        if facts.popularity_rank is not None
        else 0
    )
    momentum = _bounded(15 - abs(facts.change_pct - 4) * 1.4, 0, 15)
    if facts.return_5d_pct is not None:
        momentum = (
            momentum
            + _bounded(15 - abs(facts.return_5d_pct - 7) * 0.8, 0, 15)
        ) / 2
    ratio = facts.volume_ratio_5d or 0
    volume_expansion = (
        _bounded(10 - abs(ratio - 2) * 4, 0, 10) if ratio > 0 else 0
    )
    distance = facts.distance_20d_high_pct
    structure = _bounded(10 - abs(distance or -20) * 0.8, 0, 10)
    if facts.ma_alignment == "bullish":
        structure += 2
    structure += facts.close_location * 3
    structure = _bounded(structure, 0, 15)
    data_quality = 5 if facts.kline_bar_count >= 60 else 3
    return [
        ScoreBreakdownItem(
            name="题材强度",
            score=round(theme_strength, 2),
            max_score=30,
            evidence=[
                "命中"
                + "、".join(
                    f"{theme.name}({theme.change_pct:+.1f}%)"
                    for theme in facts.themes[:3]
                )
            ],
        ),
        ScoreBreakdownItem(
            name="新闻催化",
            score=round(catalyst, 2),
            max_score=15,
            evidence=[
                facts.news_catalysts[0]
                if facts.news_catalysts
                else "近 48 小时未匹配到明确题材催化"
            ],
        ),
        ScoreBreakdownItem(
            name="市场关注度",
            score=round(popularity, 2),
            max_score=10,
            evidence=[
                f"同花顺热股榜第 {facts.popularity_rank} 名"
                if facts.popularity_rank is not None
                else "未进入同花顺热股 Top100"
            ],
        ),
        ScoreBreakdownItem(
            name="短期动量",
            score=round(momentum, 2),
            max_score=15,
            evidence=[
                f"当日涨幅 {facts.change_pct:+.1f}%，近 5 日 {facts.return_5d_pct or 0:+.1f}%"
            ],
        ),
        ScoreBreakdownItem(
            name="量能扩张",
            score=round(volume_expansion, 2),
            max_score=10,
            evidence=[f"近 5 日量比 {facts.volume_ratio_5d or 0:.2f}"],
        ),
        ScoreBreakdownItem(
            name="位置结构",
            score=round(structure, 2),
            max_score=15,
            evidence=[
                f"距 20 日高点 {facts.distance_20d_high_pct or 0:+.1f}%，"
                f"均线结构{_ma_alignment_label(facts.ma_alignment)}，"
                f"收盘位置{facts.close_location:.0%}"
            ],
        ),
        ScoreBreakdownItem(
            name="数据完整性",
            score=data_quality,
            max_score=5,
            evidence=[f"可用日 K {facts.kline_bar_count} 根"],
        ),
    ]


def _theme_priority(themes: list[FirstBoardDiscoveryTheme]) -> float:
    if not themes:
        return 0
    contributions = [
        min(
            26 if theme.category == "concept" else 23,
            (15 if theme.category == "concept" else 12)
            + max(0, theme.change_pct) * 1.8
            + (3 if theme.news_headlines else 0),
        )
        for theme in themes
    ]
    return min(30, max(contributions) + min(6, (len(themes) - 1) * 2))


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
