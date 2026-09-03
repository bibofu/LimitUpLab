"""Refresh mutable market, news and financial facts for recommendations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    AgentPrediction,
    FirstBoardRating,
    FirstBoardRatingsResponse,
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
DISCOVERY_POOL_SIZE = 50
DISCOVERY_DISPLAY_LIMIT = 15
RELAY_DISPLAY_LIMIT = 10
FINANCIAL_CACHE_TTL = timedelta(hours=24)
MARKET_CLOSE_TIME = time(15, 0)
FINALIZATION_TIME = time(9, 0)
MARKET_OPEN_TIME = time(9, 30)
MAX_RELAY_DYNAMIC_ADJUSTMENT = 6.0
MISSED_CUTOFF_WARNING = (
    "盘前推荐未在 09:30 开盘前固化，已停止更新；"
    "不会使用开盘后行情、新闻或人气数据补算盘前排名。"
)

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
    first_limit_time: time | None = None
    break_count: int | None = None
    turnover_rate: float | None = None
    confidence: float | None = None
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
    target_trade_date = _target_trade_date(
        discovery_repository=discovery_repo,
        base_trade_date=max(
            (item for item in (discovery_date, relay_date) if item is not None),
            default=None,
        ),
    )
    previous = intelligence_repo.get_latest()
    same_basis = _matches_recommendation_basis(
        previous,
        target_trade_date=target_trade_date,
        discovery_base_date=discovery_date,
        relay_base_date=relay_date,
    )
    if (
        same_basis
        and previous is not None
        and previous.stage == "final"
        and _was_refreshed_before_market_open(previous)
    ):
        return previous
    if same_basis and previous is not None and previous.stage == "missed_cutoff":
        return previous
    if _premarket_refresh_window_closed(
        target_trade_date=target_trade_date,
        now=refreshed_at,
    ):
        missed = _missed_cutoff_response(
            previous=previous if same_basis else None,
            refreshed_at=refreshed_at,
            interval_minutes=interval_minutes,
            target_trade_date=target_trade_date,
            discovery_base_date=discovery_date,
            relay_base_date=relay_date,
        )
        intelligence_repo.save(missed)
        return missed
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
    popularity_ready = not unique_candidates
    if unique_candidates:
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

    previous_financials = {
        item.symbol: item.financial_report
        for item in previous.items
        if item.financial_report is not None
    } if previous else {}
    previous_news = {
        item.symbol: StockNewsFacts(
            symbol=item.symbol,
            name=item.name,
            fetched_at=item.refreshed_at,
            window_days=7,
            cache_status="stale",
            sources=list(dict.fromkeys(news.source for news in item.latest_news)),
            items=item.latest_news,
            data_missing=["新闻刷新失败，沿用上一轮结果"],
        )
        for item in previous.items
        if item.latest_news
    } if previous else {}
    previous_items = {
        (item.strategy, item.base_trade_date, item.symbol): item
        for item in previous.items
    } if previous else {}
    evidence_by_symbol = _collect_candidate_evidence(
        list(unique_candidates.values()),
        refreshed_at=refreshed_at,
        previous_financials=previous_financials,
        previous_news=previous_news,
        news_collector=active_news_collector,
        financial_collector=active_financial_collector,
        max_workers=max_workers,
    )

    items: list[RecommendationIntelligenceItem] = []
    provider_error_count = 0
    for candidate in candidates:
        quote = quote_by_symbol.get(candidate.symbol)
        previous_item = previous_items.get(
            (candidate.strategy, candidate.base_trade_date, candidate.symbol)
        )
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
            if current_dragon_tiger is None and previous_item is not None:
                if previous_item.dragon_tiger_on_list:
                    current_dragon_tiger = DragonTigerFact(
                        symbol=candidate.symbol,
                        buy_amount=None,
                        sell_amount=None,
                        net_buy_amount=previous_item.dragon_tiger_net_buy_amount,
                        float_market_cap=None,
                        reason=None,
                        source=previous_item.dragon_tiger_source or "previous-refresh",
                    )
            if (
                current_popularity is None
                and current_dragon_tiger is not None
                and current_dragon_tiger.hot_rank is not None
            ):
                current_popularity = PopularityFact(
                    symbol=candidate.symbol,
                    rank=current_dragon_tiger.hot_rank,
                    rank_change=None,
                    captured_at=refreshed_at,
                    source=f"{current_dragon_tiger.source}-dragon-tiger",
                )
            if (
                current_popularity is None
                and not popularity_ready
                and previous_item is not None
                and previous_item.popularity_rank is not None
            ):
                current_popularity = PopularityFact(
                    symbol=candidate.symbol,
                    rank=previous_item.popularity_rank,
                    rank_change=previous_item.popularity_rank_change,
                    captured_at=(
                        previous_item.popularity_snapshot_at
                        or previous_item.refreshed_at
                    ),
                    source=previous_item.popularity_source or "previous-refresh",
                )
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
            current_popularity = popularity_by_symbol.get(candidate.symbol)
            if (
                current_popularity is None
                and not popularity_ready
                and previous_item is not None
                and previous_item.popularity_rank is not None
            ):
                current_popularity = PopularityFact(
                    symbol=candidate.symbol,
                    rank=previous_item.popularity_rank,
                    rank_change=previous_item.popularity_rank_change,
                    captured_at=(
                        previous_item.popularity_snapshot_at
                        or previous_item.refreshed_at
                    ),
                    source=previous_item.popularity_source or "previous-refresh",
                )
            if not popularity_ready:
                missing.append("最新人气榜刷新不可用")
            popularity_adjustment, popularity_reasons = (
                _discovery_popularity_adjustment(current_popularity)
                if popularity_ready
                else (0.0, [])
            )
            popularity_rank_change = None
            current_dragon_tiger = None
            dynamic_adjustment = round(
                news_adjustment + financial_adjustment + popularity_adjustment,
                1,
            )
            update_reasons = [
                *news_reasons,
                *financial_reasons,
                *(f"人气变化：{reason}" for reason in popularity_reasons),
            ]
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
                first_limit_time=candidate.first_limit_time,
                break_count=candidate.break_count,
                turnover_rate=candidate.turnover_rate,
                amount=candidate.amount,
                confidence=candidate.confidence,
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
                current_price=(
                    quote.last_price
                    if quote
                    else previous_item.current_price if previous_item else None
                ),
                change_pct=(
                    quote.change_pct
                    if quote
                    else previous_item.change_pct if previous_item else None
                ),
                turnover=(
                    quote.turnover
                    if quote
                    else previous_item.turnover if previous_item else None
                ),
                quote_captured_at=(
                    quote_captured_at
                    if quote
                    else previous_item.quote_captured_at if previous_item else None
                ),
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
        target_trade_date=target_trade_date,
        discovery_pool_size=sum(
            item.strategy == "discovery" for item in ranked_items
        ),
        discovery_display_limit=DISCOVERY_DISPLAY_LIMIT,
        relay_pool_size=sum(item.strategy == "relay" for item in ranked_items),
        relay_display_limit=RELAY_DISPLAY_LIMIT,
        popularity_coverage_count=len(popularity_by_symbol),
        status="partial" if warnings else "complete",
        discovery_base_date=discovery_date,
        relay_base_date=relay_date,
        items=ranked_items,
        warnings=warnings,
    )
    intelligence_repo.save(response)
    return response


def should_finalize_recommendation_intelligence(
    response: RecommendationIntelligenceResponse,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a draft is inside the pre-open finalization window."""

    local_now = _as_shanghai(now or datetime.now(SHANGHAI_TZ))
    return (
        response.stage == "draft"
        and response.target_trade_date == local_now.date()
        and local_now.time() >= FINALIZATION_TIME
        and local_now.time() < MARKET_OPEN_TIME
    )


def finalize_recommendation_intelligence(
    response: RecommendationIntelligenceResponse,
    *,
    now: datetime | None = None,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    snapshot_repository: SQLiteRecommendationIntelligenceRepository | None = None,
) -> RecommendationIntelligenceResponse:
    """Freeze display rankings and replace the relay review baseline once."""

    finalized_at = _as_shanghai(now or datetime.now(SHANGHAI_TZ))
    if response.stage == "final":
        return response
    if response.target_trade_date is None:
        raise ValueError("Cannot finalize a recommendation without target_trade_date.")
    if not should_finalize_recommendation_intelligence(response, now=finalized_at):
        raise ValueError(
            "Recommendation finalization is only allowed from 09:00 until "
            "the 09:30 market open on the target trading day."
        )

    first_repo = first_board_repository or SQLiteFirstBoardRepository()
    intelligence_repo = snapshot_repository or (
        SQLiteRecommendationIntelligenceRepository(first_repo.database_path)
    )
    existing = intelligence_repo.get_final(response.target_trade_date.isoformat())
    if existing is not None:
        intelligence_repo.save(existing)
        return existing

    selected_items = [
        *sorted(
            (item for item in response.items if item.strategy == "discovery"),
            key=lambda item: (item.rank, item.symbol),
        )[: response.discovery_display_limit],
        *sorted(
            (item for item in response.items if item.strategy == "relay"),
            key=lambda item: (item.rank, item.symbol),
        )[: response.relay_display_limit],
    ]
    final = response.model_copy(
        update={
            "stage": "final",
            "finalized_at": finalized_at,
            "refreshed_at": finalized_at,
            "items": selected_items,
        }
    )
    _persist_final_relay_snapshot(
        final,
        limit_up_repository=(
            limit_up_repository
            or SQLiteLimitUpRepository(first_repo.database_path, seed_if_empty=False)
        ),
        first_board_repository=first_repo,
    )
    if not intelligence_repo.save_final(final):
        persisted = intelligence_repo.get_final(response.target_trade_date.isoformat())
        if persisted is not None:
            intelligence_repo.save(persisted)
            return persisted
        return final
    return final


def _matches_recommendation_basis(
    response: RecommendationIntelligenceResponse | None,
    *,
    target_trade_date: date | None,
    discovery_base_date: date | None,
    relay_base_date: date | None,
) -> bool:
    """Return whether a persisted snapshot belongs to the current prediction day."""

    return bool(
        response is not None
        and response.target_trade_date == target_trade_date
        and response.discovery_base_date == discovery_base_date
        and response.relay_base_date == relay_base_date
    )


def _premarket_refresh_window_closed(
    *,
    target_trade_date: date | None,
    now: datetime,
) -> bool:
    """Prevent a missing pre-market snapshot from being rebuilt after the open."""

    if target_trade_date is None:
        return False
    local_now = _as_shanghai(now)
    return local_now >= datetime.combine(
        target_trade_date,
        MARKET_OPEN_TIME,
        tzinfo=SHANGHAI_TZ,
    )


def _was_refreshed_before_market_open(
    response: RecommendationIntelligenceResponse,
) -> bool:
    """Reject legacy finals whose evidence was first collected after the open."""

    if response.target_trade_date is None:
        return False
    return _as_shanghai(response.refreshed_at) < datetime.combine(
        response.target_trade_date,
        MARKET_OPEN_TIME,
        tzinfo=SHANGHAI_TZ,
    )


def _missed_cutoff_response(
    *,
    previous: RecommendationIntelligenceResponse | None,
    refreshed_at: datetime,
    interval_minutes: int,
    target_trade_date: date | None,
    discovery_base_date: date | None,
    relay_base_date: date | None,
) -> RecommendationIntelligenceResponse:
    """Persist an explicit non-prediction instead of fabricating a late final."""

    safe_previous = (
        previous
        if previous is not None
        and previous.stage == "draft"
        and _was_refreshed_before_market_open(previous)
        else None
    )
    if safe_previous is not None:
        return safe_previous.model_copy(
            update={
                "refresh_id": f"recommendation_missed_{uuid4().hex}",
                "stage": "missed_cutoff",
                "status": "partial",
                "finalized_at": None,
                "warnings": list(
                    dict.fromkeys([*safe_previous.warnings, MISSED_CUTOFF_WARNING])
                ),
            }
        )
    return RecommendationIntelligenceResponse(
        refresh_id=f"recommendation_missed_{uuid4().hex}",
        refreshed_at=refreshed_at,
        interval_minutes=max(5, min(interval_minutes, 1440)),
        stage="missed_cutoff",
        target_trade_date=target_trade_date,
        finalized_at=None,
        status="partial",
        discovery_base_date=discovery_base_date,
        relay_base_date=relay_base_date,
        items=[],
        warnings=[MISSED_CUTOFF_WARNING],
    )


def _persist_final_relay_snapshot(
    response: RecommendationIntelligenceResponse,
    *,
    limit_up_repository: SQLiteLimitUpRepository,
    first_board_repository: SQLiteFirstBoardRepository,
) -> None:
    """Make the pre-open relay Top10 the sole live snapshot used by review."""

    trade_date = response.relay_base_date
    if trade_date is None or response.target_trade_date is None:
        return
    existing = first_board_repository.get_live_prediction_snapshot(trade_date)
    scoring_policy = (
        SQLiteScoringPolicyRepository(
            first_board_repository.database_path
        ).get_policy(existing.generated_by)
        if existing is not None
        else None
    )
    rebuilt = build_first_board_ratings(
        events=limit_up_repository.list_events(),
        trade_date=trade_date,
        first_board_repository=first_board_repository,
        scoring_policy=scoring_policy,
    )
    ratings_source = (
        rebuilt
        if rebuilt.candidates
        else existing
    )
    if ratings_source is None:
        ratings_source = build_first_board_ratings(
            events=limit_up_repository.list_events(),
            trade_date=trade_date,
            first_board_repository=first_board_repository,
        )
    rating_by_symbol = {
        item.facts.symbol: item
        for item in ratings_source.candidates
    }
    selected: list[FirstBoardRating] = []
    for dynamic in sorted(
        (item for item in response.items if item.strategy == "relay"),
        key=lambda item: (item.rank, item.symbol),
    )[: response.relay_display_limit]:
        rating = rating_by_symbol.get(dynamic.symbol)
        if rating is None:
            continue
        selected.append(
            rating.model_copy(
                update={
                    "score": dynamic.draft_score,
                    "rating": _rating_for_dynamic_score(dynamic.draft_score),
                    "reasons": list(
                        dict.fromkeys([*dynamic.update_reasons, *rating.reasons])
                    ),
                }
            )
        )
    created_at = (response.finalized_at or response.refreshed_at).astimezone(
        timezone.utc
    )
    ratings = FirstBoardRatingsResponse(
        trade_date=trade_date,
        candidates=selected,
        filtered_out=ratings_source.filtered_out,
        universe_count=ratings_source.universe_count,
        generated_by=ratings_source.generated_by,
        snapshot_source="live",
        data_as_of=response.target_trade_date,
        snapshot_created_at=created_at,
    )
    predictions = [
        AgentPrediction(
            prediction_id=(
                f"{trade_date.isoformat()}-{rating.facts.symbol}-"
                f"{ratings_source.generated_by}-live"
            ),
            trade_date=trade_date,
            symbol=rating.facts.symbol,
            name=rating.facts.name,
            score=rating.score,
            rating=rating.rating,
            confidence=rating.confidence,
            scoring_version=ratings_source.generated_by,
            prediction_source="live",
            data_as_of=response.target_trade_date,
            facts_json=rating.facts.model_dump(mode="json"),
            reasons=rating.reasons,
            risks=rating.risks,
            created_at=created_at,
        )
        for rating in selected
    ]
    first_board_repository.persist_live_prediction_snapshot(
        ratings=ratings,
        predictions=predictions,
        top_limit=response.relay_display_limit,
        data_as_of=response.target_trade_date,
        created_at=created_at,
        replace=True,
    )


def _rating_for_dynamic_score(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


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
            for index, item in enumerate(
                discovery.candidates[:DISCOVERY_POOL_SIZE],
                start=1,
            )
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
                first_limit_time=item.facts.first_limit_time,
                break_count=item.facts.break_count,
                turnover_rate=item.facts.turnover_rate,
                confidence=item.confidence,
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
            for index, item in enumerate(relay_candidates, start=1)
        )
    return candidates, discovery_date, relay_date, warnings


def _target_trade_date(
    *,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository,
    base_trade_date: date | None,
) -> date | None:
    """Use the persisted exchange-calendar target, with a weekday fallback."""

    discovery = discovery_repository.get_latest(FIRST_BOARD_DISCOVERY_VERSION)
    if (
        discovery is not None
        and discovery.data_as_of == base_trade_date
        and discovery.target_trade_date is not None
    ):
        return discovery.target_trade_date
    if base_trade_date is None:
        return None
    target = base_trade_date + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


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


def _discovery_popularity_adjustment(
    current: PopularityFact | None,
) -> tuple[float, list[str]]:
    """Use a covered live rank as one bounded low-position reranking signal."""

    if current is None:
        return 0.0, []
    if current.rank <= 10:
        adjustment = 2.0
    elif current.rank <= 30:
        adjustment = 1.0
    else:
        adjustment = 0.0
    return (
        adjustment,
        [f"当前榜单第 {current.rank} 名，动态修正 {adjustment:+g} 分"]
        if adjustment
        else [],
    )


def _popularity_adjustment(
    candidate: _BaseCandidate,
    current: PopularityFact | None,
    *,
    captured_at: datetime | None,
) -> tuple[float, list[str], int | None]:
    """Compare two covered popularity ranks without inferring missing positions."""

    base_rank = candidate.popularity_rank
    base_snapshot_at = candidate.popularity_snapshot_at
    if base_rank is None or base_snapshot_at is None or captured_at is None:
        return 0.0, [], None
    if _as_shanghai(captured_at) <= _as_shanghai(base_snapshot_at):
        return 0.0, [], None

    if current is None:
        return 0.0, [], None
    current_rank = current.rank
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
    previous_news: dict[str, StockNewsFacts],
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
                previous_news=previous_news.get(candidate.symbol),
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
    previous_news: StockNewsFacts | None,
    news_collector: NewsCollector,
    financial_collector: FinancialCollector,
) -> _CandidateEvidence:
    errors: list[str] = []
    try:
        news = news_collector(candidate.symbol, candidate.name)
        if news.cache_status == "stale" and news.data_missing:
            errors.extend(news.data_missing[:1])
    except Exception as error:  # noqa: BLE001
        news = previous_news
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
