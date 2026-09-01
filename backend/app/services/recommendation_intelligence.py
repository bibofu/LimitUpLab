"""Refresh mutable market, news and financial facts for recommendations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.agents.first_board import build_first_board_ratings
from app.collectors.first_board_enrichment_collector import (
    DragonTigerFact,
    PopularityFact,
    collect_preferred_dragon_tiger_facts,
    collect_preferred_popularity,
)
from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkIncomeStatementFact,
    HithinkMarketSnapshot,
)
from app.models import (
    RecommendationFinancialReport,
    RecommendationIntelligenceItem,
    RecommendationIntelligenceResponse,
    StockNewsFacts,
)
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteRecommendationIntelligenceRepository,
    SQLiteScoringPolicyRepository,
)
from app.services.stock_news import collect_stock_news
from app.services.first_board_discovery import FIRST_BOARD_DISCOVERY_VERSION
from app.services.relay_universe import is_relay_candidate_symbol


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_REFRESH_INTERVAL_MINUTES = 30
FINANCIAL_CACHE_TTL = timedelta(hours=24)
MARKET_CLOSE_TIME = time(15, 0)
MAX_RELAY_DYNAMIC_ADJUSTMENT = 6.0

QuoteCollector = Callable[[Sequence[str]], HithinkMarketSnapshot]
NewsCollector = Callable[[str, str], StockNewsFacts]
FinancialCollector = Callable[[str], list[HithinkIncomeStatementFact]]
DragonTigerCollector = Callable[[date], dict[str, DragonTigerFact]]
PopularityCollector = Callable[[], dict[str, PopularityFact]]


@dataclass(frozen=True)
class _BaseCandidate:
    strategy: str
    base_trade_date: date
    symbol: str
    name: str
    sector: str
    position_label: str | None
    rank: int
    base_score: float
    amount: float | None = None
    dragon_tiger_on_list: bool = False
    dragon_tiger_net_buy_amount: float | None = None
    dragon_tiger_source: str | None = None
    popularity_baseline_ready: bool = False
    popularity_rank: int | None = None
    popularity_snapshot_at: datetime | None = None
    popularity_source: str | None = None


@dataclass(frozen=True)
class _CandidateEvidence:
    news: StockNewsFacts | None
    financial_report: RecommendationFinancialReport | None
    errors: list[str]


def refresh_recommendation_intelligence(
    *,
    interval_minutes: int = DEFAULT_REFRESH_INTERVAL_MINUTES,
    max_workers: int = 6,
    now: datetime | None = None,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository | None = None,
    snapshot_repository: SQLiteRecommendationIntelligenceRepository | None = None,
    quote_collector: QuoteCollector | None = None,
    news_collector: NewsCollector | None = None,
    financial_collector: FinancialCollector | None = None,
    dragon_tiger_collector: DragonTigerCollector | None = None,
    popularity_collector: PopularityCollector | None = None,
) -> RecommendationIntelligenceResponse:
    """Rebuild the mutable draft from point-in-time market evidence."""

    refreshed_at = _as_shanghai(now or datetime.now(SHANGHAI_TZ))
    first_repo = first_board_repository or SQLiteFirstBoardRepository()
    limit_repo = limit_up_repository or SQLiteLimitUpRepository(seed_if_empty=False)
    discovery_repo = discovery_repository or SQLiteFirstBoardDiscoveryRepository(
        first_repo.database_path
    )
    intelligence_repo = snapshot_repository or (
        SQLiteRecommendationIntelligenceRepository(first_repo.database_path)
    )
    candidates, discovery_date, relay_date, warnings = _load_base_candidates(
        limit_up_repository=limit_repo,
        first_board_repository=first_repo,
        discovery_repository=discovery_repo,
    )
    hithink = HithinkFinanceCollector()
    active_quote_collector = quote_collector or hithink.collect_market_snapshots
    active_news_collector = news_collector or (
        lambda symbol, name: collect_stock_news(
            symbol=symbol,
            name=name,
            days=7,
            limit=3,
        )
    )
    active_financial_collector = financial_collector or (
        lambda thscode: hithink.collect_income_statements(thscode, limit=6)
    )
    active_dragon_tiger_collector = (
        dragon_tiger_collector or collect_preferred_dragon_tiger_facts
    )
    active_popularity_collector = popularity_collector or collect_preferred_popularity

    unique_candidates = {
        item.symbol: item
        for item in candidates
    }
    quote_by_symbol = {}
    quote_captured_at: datetime | None = None
    if unique_candidates:
        try:
            market = active_quote_collector(
                [_to_thscode(symbol) for symbol in unique_candidates]
            )
            quote_by_symbol = {item.symbol: item for item in market.items}
            quote_captured_at = market.captured_at
        except Exception as error:  # noqa: BLE001
            warnings.append(f"最新行情刷新失败：{error}")

    relay_candidates = [item for item in candidates if item.strategy == "relay"]
    dragon_tiger_by_symbol: dict[str, DragonTigerFact] = {}
    dragon_tiger_ready = not relay_candidates
    if relay_candidates and relay_date is not None:
        try:
            dragon_tiger_by_symbol = active_dragon_tiger_collector(relay_date)
            dragon_tiger_ready = True
        except Exception as error:  # noqa: BLE001
            warnings.append(f"龙虎榜刷新失败：{error}")

    popularity_by_symbol: dict[str, PopularityFact] = {}
    popularity_captured_at: datetime | None = None
    popularity_ready = not relay_candidates
    if relay_candidates:
        try:
            popularity_by_symbol = active_popularity_collector()
            if not popularity_by_symbol:
                raise RuntimeError("人气榜返回空数据")
            popularity_captured_at = max(
                (item.captured_at for item in popularity_by_symbol.values()),
            )
            popularity_ready = True
        except Exception as error:  # noqa: BLE001
            warnings.append(f"人气榜刷新失败：{error}")

    previous = intelligence_repo.get_latest()
    previous_financials = {
        item.symbol: item.financial_report
        for item in previous.items
        if item.financial_report is not None
    } if previous else {}
    evidence_by_symbol = _collect_candidate_evidence(
        list(unique_candidates.values()),
        refreshed_at=refreshed_at,
        previous_financials=previous_financials,
        news_collector=active_news_collector,
        financial_collector=active_financial_collector,
        max_workers=max_workers,
    )

    items: list[RecommendationIntelligenceItem] = []
    provider_error_count = 0
    for candidate in candidates:
        quote = quote_by_symbol.get(candidate.symbol)
        evidence = evidence_by_symbol.get(
            candidate.symbol,
            _CandidateEvidence(None, None, ["情报刷新未返回结果"]),
        )
        missing = list(evidence.errors)
        if quote is None:
            missing.append("最新行情不可用")
        if evidence.news is None or not evidence.news.items:
            missing.append("最近 7 日无直接相关新闻")
        if evidence.financial_report is None:
            missing.append("最新季度财报不可用")
        if evidence.errors:
            provider_error_count += 1
        facts_cutoff_at = _market_close(candidate.base_trade_date)
        if candidate.strategy == "relay":
            current_dragon_tiger = dragon_tiger_by_symbol.get(candidate.symbol)
            current_popularity = popularity_by_symbol.get(candidate.symbol)
            if not dragon_tiger_ready:
                missing.append("最新龙虎榜刷新不可用")
            if not popularity_ready:
                missing.append("最新人气榜刷新不可用")
            elif not candidate.popularity_baseline_ready:
                missing.append("收盘人气基线不可用")
            close_news_adjustment, close_news_reasons = _news_adjustment(
                evidence.news,
                refreshed_at=facts_cutoff_at,
                published_at_or_before=facts_cutoff_at,
            )
            news_adjustment, news_reasons = _news_adjustment(
                evidence.news,
                refreshed_at=refreshed_at,
                published_after=facts_cutoff_at,
            )
            (
                close_financial_adjustment,
                close_financial_reasons,
                financial_adjustment,
                financial_reasons,
            ) = _split_financial_adjustment(
                evidence.financial_report,
                base_trade_date=candidate.base_trade_date,
                refreshed_at=refreshed_at,
            )
            close_information_adjustment = round(
                close_news_adjustment + close_financial_adjustment,
                1,
            )
            close_information_reasons = [
                *(f"收盘前已知：{reason}" for reason in close_news_reasons),
                *(
                    f"收盘前已知：{reason}"
                    for reason in close_financial_reasons
                ),
            ]
            dragon_tiger_adjustment, dragon_tiger_reasons = (
                _dragon_tiger_adjustment(candidate, current_dragon_tiger)
                if dragon_tiger_ready
                else (0.0, [])
            )
            (
                popularity_adjustment,
                popularity_reasons,
                popularity_rank_change,
            ) = (
                _popularity_adjustment(
                    candidate,
                    current_popularity,
                    captured_at=popularity_captured_at,
                )
                if popularity_ready
                else (0.0, [], None)
            )
            update_reasons = [
                *(f"收盘后新增：{reason}" for reason in news_reasons),
                *(
                    f"收盘后新增：{reason}"
                    for reason in financial_reasons
                ),
                *(f"收盘后新增：{reason}" for reason in dragon_tiger_reasons),
                *(f"人气变化：{reason}" for reason in popularity_reasons),
            ]
            raw_dynamic_adjustment = round(
                news_adjustment
                + financial_adjustment
                + dragon_tiger_adjustment
                + popularity_adjustment,
                1,
            )
            dynamic_adjustment = _bounded_adjustment(
                raw_dynamic_adjustment,
                limit=MAX_RELAY_DYNAMIC_ADJUSTMENT,
            )
            if dynamic_adjustment != raw_dynamic_adjustment:
                update_reasons.append(
                    "盘后动态修正受 ±"
                    f"{MAX_RELAY_DYNAMIC_ADJUSTMENT:g} 分约束，"
                    f"原始合计 {raw_dynamic_adjustment:+g} 分"
                )
        else:
            close_information_adjustment = 0.0
            close_information_reasons = []
            news_adjustment, news_reasons = _news_adjustment(
                evidence.news,
                refreshed_at=refreshed_at,
            )
            financial_adjustment, financial_reasons = _financial_adjustment(
                evidence.financial_report
            )
            dragon_tiger_adjustment = 0.0
            popularity_adjustment = 0.0
            popularity_rank_change = None
            current_dragon_tiger = None
            current_popularity = None
            dynamic_adjustment = round(
                news_adjustment + financial_adjustment,
                1,
            )
            update_reasons = [*news_reasons, *financial_reasons]
        base_score = _bounded_score(
            candidate.base_score + close_information_adjustment
        )
        items.append(
            RecommendationIntelligenceItem(
                strategy=candidate.strategy,
                base_trade_date=candidate.base_trade_date,
                symbol=candidate.symbol,
                name=candidate.name,
                sector=candidate.sector,
                position_label=candidate.position_label,
                rule_rank=candidate.rank,
                rule_score=candidate.base_score,
                base_rank=candidate.rank,
                rank=candidate.rank,
                base_score=base_score,
                draft_score=_bounded_score(
                    base_score + dynamic_adjustment
                ),
                facts_cutoff_at=facts_cutoff_at,
                close_information_adjustment=close_information_adjustment,
                close_information_reasons=close_information_reasons,
                news_adjustment=news_adjustment,
                financial_adjustment=financial_adjustment,
                dragon_tiger_adjustment=dragon_tiger_adjustment,
                popularity_adjustment=popularity_adjustment,
                dynamic_adjustment=dynamic_adjustment,
                dragon_tiger_on_list=(
                    current_dragon_tiger is not None
                    or candidate.dragon_tiger_on_list
                    if candidate.strategy == "relay"
                    else False
                ),
                dragon_tiger_is_new=(
                    current_dragon_tiger is not None
                    and not candidate.dragon_tiger_on_list
                    if candidate.strategy == "relay"
                    else False
                ),
                dragon_tiger_net_buy_amount=(
                    current_dragon_tiger.net_buy_amount
                    if current_dragon_tiger
                    else candidate.dragon_tiger_net_buy_amount
                ),
                dragon_tiger_source=(
                    current_dragon_tiger.source
                    if current_dragon_tiger
                    else candidate.dragon_tiger_source
                ),
                popularity_base_rank=(
                    candidate.popularity_rank
                    if candidate.popularity_rank is not None
                    else 101
                    if candidate.popularity_baseline_ready
                    else None
                ),
                popularity_rank=(
                    current_popularity.rank if current_popularity else None
                ),
                popularity_rank_change=popularity_rank_change,
                popularity_snapshot_at=(
                    current_popularity.captured_at
                    if current_popularity
                    else popularity_captured_at
                    if candidate.strategy == "relay"
                    else None
                ),
                popularity_source=(
                    current_popularity.source
                    if current_popularity
                    else candidate.popularity_source
                ),
                update_reasons=update_reasons,
                current_price=quote.last_price if quote else None,
                change_pct=quote.change_pct if quote else None,
                turnover=quote.turnover if quote else None,
                quote_captured_at=quote_captured_at if quote else None,
                latest_news=evidence.news.items[:3] if evidence.news else [],
                financial_report=evidence.financial_report,
                refreshed_at=refreshed_at,
                data_missing=list(dict.fromkeys(missing)),
            )
        )
    base_ranked_items: list[RecommendationIntelligenceItem] = []
    for strategy in ("discovery", "relay"):
        strategy_items = sorted(
            [item for item in items if item.strategy == strategy],
            key=lambda item: (-item.base_score, item.rule_rank, item.symbol),
        )
        base_ranked_items.extend(
            item.model_copy(update={"base_rank": index})
            for index, item in enumerate(strategy_items, start=1)
        )
    ranked_items: list[RecommendationIntelligenceItem] = []
    for strategy in ("discovery", "relay"):
        strategy_items = sorted(
            [item for item in base_ranked_items if item.strategy == strategy],
            key=lambda item: (-item.draft_score, item.base_rank, item.symbol),
        )
        ranked_items.extend(
            item.model_copy(update={"rank": index})
            for index, item in enumerate(strategy_items, start=1)
        )
    if provider_error_count:
        warnings.append(
            f"{provider_error_count} 只股票的新闻或财报刷新发生错误，已保留可用缓存。"
        )
    response = RecommendationIntelligenceResponse(
        refresh_id=f"recommendation_{uuid4().hex}",
        refreshed_at=refreshed_at,
        interval_minutes=max(5, min(interval_minutes, 1440)),
        status="partial" if warnings else "complete",
        discovery_base_date=discovery_date,
        relay_base_date=relay_date,
        items=ranked_items,
        warnings=warnings,
    )
    intelligence_repo.save(response)
    return response


def _load_base_candidates(
    *,
    limit_up_repository: SQLiteLimitUpRepository,
    first_board_repository: SQLiteFirstBoardRepository,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository,
) -> tuple[list[_BaseCandidate], date | None, date | None, list[str]]:
    candidates: list[_BaseCandidate] = []
    warnings: list[str] = []
    discovery = discovery_repository.get_latest(FIRST_BOARD_DISCOVERY_VERSION)
    discovery_date = discovery.data_as_of if discovery else None
    if discovery is None:
        warnings.append("低位挖掘快照不可用")
    else:
        candidates.extend(
            _BaseCandidate(
                strategy="discovery",
                base_trade_date=discovery.data_as_of,
                symbol=item.facts.symbol,
                name=item.facts.name,
                sector=item.facts.themes[0].name if item.facts.themes else "",
                position_label=item.facts.pattern,
                rank=index,
                base_score=item.score,
            )
            for index, item in enumerate(discovery.candidates[:30], start=1)
        )

    events = limit_up_repository.list_events()
    relay_date = max((item.trade_date for item in events), default=None)
    live_relay = (
        first_board_repository.get_live_prediction_snapshot(relay_date)
        if relay_date
        else None
    )
    relay = None
    if relay_date is not None:
        scoring_policy = (
            SQLiteScoringPolicyRepository(
                first_board_repository.database_path
            ).get_policy(live_relay.generated_by)
            if live_relay is not None
            else None
        )
        relay = build_first_board_ratings(
            events=events,
            trade_date=relay_date,
            first_board_repository=first_board_repository,
            scoring_policy=scoring_policy,
        )
    if relay is None:
        warnings.append("一进二接力快照不可用")
    else:
        relay_candidates = [
            item
            for item in relay.candidates
            if is_relay_candidate_symbol(item.facts.symbol)
        ]
        candidates.extend(
            _BaseCandidate(
                strategy="relay",
                base_trade_date=relay.trade_date,
                symbol=item.facts.symbol,
                name=item.facts.name,
                sector=item.facts.concept or item.facts.industry,
                position_label=(
                    item.facts.enrichment.position.primary.label
                    if item.facts.enrichment and item.facts.enrichment.position
                    else None
                ),
                rank=index,
                base_score=item.score,
                amount=item.facts.amount,
                dragon_tiger_on_list=(
                    item.facts.enrichment.dragon_tiger_on_list
                    if item.facts.enrichment
                    else False
                ),
                dragon_tiger_net_buy_amount=(
                    item.facts.enrichment.dragon_tiger_net_buy_amount
                    if item.facts.enrichment
                    else None
                ),
                dragon_tiger_source=(
                    item.facts.enrichment.dragon_tiger_source
                    if item.facts.enrichment
                    else None
                ),
                popularity_baseline_ready=(
                    item.facts.enrichment is not None
                    and "popularity_snapshot"
                    not in item.facts.enrichment.data_missing
                    and "eastmoney_popularity"
                    not in item.facts.enrichment.data_missing
                ),
                popularity_rank=(
                    item.facts.enrichment.popularity_rank
                    if item.facts.enrichment
                    else None
                ),
                popularity_snapshot_at=(
                    item.facts.enrichment.popularity_snapshot_at
                    or item.facts.enrichment.created_at
                    if item.facts.enrichment
                    else None
                ),
                popularity_source=(
                    item.facts.enrichment.popularity_source
                    if item.facts.enrichment
                    else None
                ),
            )
            for index, item in enumerate(relay_candidates[:30], start=1)
        )
    return candidates, discovery_date, relay_date, warnings


POSITIVE_NEWS_TERMS = (
    "中标",
    "签署合同",
    "订单",
    "获批",
    "回购",
    "增持",
    "预增",
    "扭亏",
    "战略合作",
    "突破",
)
NEGATIVE_NEWS_TERMS = (
    "减持",
    "亏损",
    "立案",
    "处罚",
    "风险提示",
    "终止",
    "问询",
    "诉讼",
    "下调",
    "退市",
)


def _news_adjustment(
    news: StockNewsFacts | None,
    *,
    refreshed_at: datetime,
    published_after: datetime | None = None,
    published_at_or_before: datetime | None = None,
) -> tuple[float, list[str]]:
    """Convert recent explicit company events into a bounded score adjustment."""

    if news is None or not news.items:
        return 0.0, []
    positive: list[str] = []
    negative: list[str] = []
    for item in news.items:
        text = item.title
        if item.item_type in {"announcement_report", "regulatory"}:
            text = f"{item.title} {item.summary}"
        published_at = _as_shanghai(item.published_at)
        age = refreshed_at - published_at
        if age < timedelta(0) or age > timedelta(hours=48):
            continue
        if published_after is not None and published_at <= published_after:
            continue
        if (
            published_at_or_before is not None
            and published_at > published_at_or_before
        ):
            continue
        if any(term in text for term in POSITIVE_NEWS_TERMS):
            positive.append(item.title)
        if any(term in text for term in NEGATIVE_NEWS_TERMS):
            negative.append(item.title)
    adjustment = min(6.0, len(positive) * 3.0) - min(8.0, len(negative) * 4.0)
    reasons = []
    if positive:
        reasons.append(f"近48小时积极事件：{positive[0]}")
    if negative:
        reasons.append(f"近48小时风险事件：{negative[0]}")
    return round(adjustment, 1), reasons


def _financial_adjustment(
    report: RecommendationFinancialReport | None,
) -> tuple[float, list[str]]:
    """Apply a small bounded adjustment from the latest comparable report."""

    if report is None or report.net_profit_yoy_pct is None:
        return 0.0, []
    growth = report.net_profit_yoy_pct
    if growth >= 50:
        return 3.0, [f"归母净利润同比增长 {growth:.1f}%"]
    if growth >= 20:
        return 2.0, [f"归母净利润同比增长 {growth:.1f}%"]
    if growth > 0:
        return 1.0, [f"归母净利润同比增长 {growth:.1f}%"]
    if growth <= -50:
        return -4.0, [f"归母净利润同比下降 {abs(growth):.1f}%"]
    if growth < 0:
        return -2.0, [f"归母净利润同比下降 {abs(growth):.1f}%"]
    return 0.0, []


def _split_financial_adjustment(
    report: RecommendationFinancialReport | None,
    *,
    base_trade_date: date,
    refreshed_at: datetime,
) -> tuple[float, list[str], float, list[str]]:
    """Separate already-known reports from genuinely post-close reports."""

    adjustment, reasons = _financial_adjustment(report)
    if report is None or adjustment == 0:
        return 0.0, [], 0.0, []
    if report.report_date <= base_trade_date:
        return adjustment, reasons, 0.0, []
    if report.report_date <= _as_shanghai(refreshed_at).date():
        return 0.0, [], adjustment, reasons
    return 0.0, [], 0.0, []


def _bounded_score(value: float) -> float:
    """Clamp a score to the public 0-100 range."""

    return round(max(0, min(100, value)), 1)


def _bounded_adjustment(value: float, *, limit: float) -> float:
    """Clamp a mutable adjustment without altering its component evidence."""

    return round(max(-limit, min(limit, value)), 1)


def _dragon_tiger_adjustment(
    candidate: _BaseCandidate,
    current: DragonTigerFact | None,
) -> tuple[float, list[str]]:
    """Score only a Dragon-Tiger record absent from the immutable base facts."""

    if current is None or candidate.dragon_tiger_on_list:
        return 0.0, []
    net_buy = current.net_buy_amount
    if net_buy is None:
        return 0.0, ["新上龙虎榜，但净买额缺失，不作分数修正"]
    ratio = (
        net_buy / candidate.amount
        if candidate.amount is not None and candidate.amount > 0
        else None
    )
    if ratio is not None and ratio >= 0.05:
        adjustment = 2.0
    elif ratio is not None and ratio >= 0.02:
        adjustment = 1.0
    elif ratio is not None and ratio <= -0.05:
        adjustment = -2.0
    elif ratio is not None and ratio <= -0.02:
        adjustment = -1.0
    elif ratio is None and net_buy >= 50_000_000:
        adjustment = 1.0
    elif ratio is None and net_buy <= -50_000_000:
        adjustment = -1.0
    else:
        adjustment = 0.0
    ratio_text = f"，占当日成交额 {ratio * 100:.1f}%" if ratio is not None else ""
    reason = (
        f"新上龙虎榜，净买额 {_format_money(net_buy)}{ratio_text}，"
        f"动态修正 {adjustment:+g} 分"
    )
    return adjustment, [reason]


def _popularity_adjustment(
    candidate: _BaseCandidate,
    current: PopularityFact | None,
    *,
    captured_at: datetime | None,
) -> tuple[float, list[str], int | None]:
    """Compare a fresh Top100 rank with the persisted base popularity snapshot."""

    base_rank = (
        candidate.popularity_rank
        if candidate.popularity_rank is not None
        else 101
        if candidate.popularity_baseline_ready
        else None
    )
    base_snapshot_at = candidate.popularity_snapshot_at
    if base_rank is None or base_snapshot_at is None or captured_at is None:
        return 0.0, [], None
    if _as_shanghai(captured_at) <= _as_shanghai(base_snapshot_at):
        return 0.0, [], None

    current_rank = current.rank if current is not None else 101
    rank_change = base_rank - current_rank
    if rank_change >= 50 and current_rank <= 20:
        adjustment = 2.0
    elif rank_change >= 20 and current_rank <= 50:
        adjustment = 1.0
    elif rank_change <= -50:
        adjustment = -2.0
    elif rank_change <= -20:
        adjustment = -1.0
    else:
        adjustment = 0.0
    if current is None:
        reason = (
            f"由第 {base_rank} 名降至 Top100 之外，"
            f"动态修正 {adjustment:+g} 分"
        )
    else:
        direction = "上升" if rank_change > 0 else "下降" if rank_change < 0 else "持平"
        reason = (
            f"由第 {base_rank} 名{direction}至第 {current.rank} 名，"
            f"动态修正 {adjustment:+g} 分"
        )
    return adjustment, [reason] if adjustment != 0 else [], rank_change


def _format_money(value: float) -> str:
    """Format market cash values with a compact Chinese unit."""

    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:+.2f} 亿元"
    return f"{value / 10_000:+.0f} 万元"


def _market_close(trade_date: date) -> datetime:
    """Return the A-share close cutoff used by recommendation evidence."""

    return datetime.combine(trade_date, MARKET_CLOSE_TIME, tzinfo=SHANGHAI_TZ)


def _collect_candidate_evidence(
    candidates: list[_BaseCandidate],
    *,
    refreshed_at: datetime,
    previous_financials: dict[str, RecommendationFinancialReport],
    news_collector: NewsCollector,
    financial_collector: FinancialCollector,
    max_workers: int,
) -> dict[str, _CandidateEvidence]:
    results: dict[str, _CandidateEvidence] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 10))) as executor:
        futures = {
            executor.submit(
                _load_candidate_evidence,
                candidate,
                refreshed_at=refreshed_at,
                previous_financial=previous_financials.get(candidate.symbol),
                news_collector=news_collector,
                financial_collector=financial_collector,
            ): candidate.symbol
            for candidate in candidates
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as error:  # noqa: BLE001
                results[symbol] = _CandidateEvidence(
                    news=None,
                    financial_report=previous_financials.get(symbol),
                    errors=[str(error)],
                )
    return results


def _load_candidate_evidence(
    candidate: _BaseCandidate,
    *,
    refreshed_at: datetime,
    previous_financial: RecommendationFinancialReport | None,
    news_collector: NewsCollector,
    financial_collector: FinancialCollector,
) -> _CandidateEvidence:
    errors: list[str] = []
    try:
        news = news_collector(candidate.symbol, candidate.name)
        if news.cache_status == "stale" and news.data_missing:
            errors.extend(news.data_missing[:1])
    except Exception as error:  # noqa: BLE001
        news = None
        errors.append(f"新闻刷新失败：{error}")

    financial = previous_financial
    financial_is_fresh = (
        financial is not None
        and refreshed_at - _as_shanghai(financial.fetched_at) <= FINANCIAL_CACHE_TTL
    )
    if not financial_is_fresh:
        try:
            statements = financial_collector(_to_thscode(candidate.symbol))
            financial = _build_financial_report(statements, refreshed_at)
            if financial is None:
                errors.append("财报源未返回季度利润表")
        except Exception as error:  # noqa: BLE001
            errors.append(f"财报刷新失败：{error}")
    return _CandidateEvidence(news, financial, errors)


def _build_financial_report(
    statements: list[HithinkIncomeStatementFact],
    fetched_at: datetime,
) -> RecommendationFinancialReport | None:
    if not statements:
        return None
    latest = max(statements, key=lambda item: (item.period_end, item.report_date))
    previous = next(
        (
            item
            for item in statements
            if item.fiscal_year == latest.fiscal_year - 1
            and item.fiscal_period == latest.fiscal_period
        ),
        None,
    )
    return RecommendationFinancialReport(
        fiscal_year=latest.fiscal_year,
        fiscal_period=latest.fiscal_period,
        report_date=latest.report_date,
        period_end=latest.period_end,
        operating_income=latest.operating_income,
        net_profit=latest.net_profit,
        parent_holder_net_profit=latest.parent_holder_net_profit,
        basic_eps=latest.basic_eps,
        operating_income_yoy_pct=_growth_pct(
            latest.operating_income,
            previous.operating_income if previous else None,
        ),
        net_profit_yoy_pct=_growth_pct(
            latest.parent_holder_net_profit,
            previous.parent_holder_net_profit if previous else None,
        ),
        fetched_at=fetched_at,
    )


def _growth_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _to_thscode(symbol: str) -> str:
    return f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)
