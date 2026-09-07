"""Health checks for Agent data dependencies."""

from collections import Counter
from datetime import date

from app.agents.first_board import build_first_board_ratings
from app.models import (
    AgentDataHealthResponse,
    AgentDataHealthTopCandidate,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import latest_trade_date
from app.services.outcome_completeness import build_top10_outcome_completeness


def build_agent_data_health(
    events: list[LimitUpEvent],
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    trade_date: date | None = None,
    top_limit: int = 5,
) -> AgentDataHealthResponse:
    """Build health status for raw events and first-board scoring inputs."""

    repository = first_board_repository or SQLiteFirstBoardRepository()
    warnings: list[str] = []
    if not events:
        return AgentDataHealthResponse(
            trade_date=trade_date,
            status="missing",
            raw_events_ready=False,
            raw_event_count=0,
            first_board_features_ready=False,
            first_board_feature_count=0,
            top_candidates_checked=0,
            warnings=["No local limit-up events are available."],
        )

    target_date = trade_date or latest_trade_date(events)
    raw_events = [event for event in events if event.trade_date == target_date]
    if not raw_events:
        return AgentDataHealthResponse(
            trade_date=target_date,
            status="missing",
            raw_events_ready=False,
            raw_event_count=0,
            first_board_features_ready=False,
            first_board_feature_count=0,
            top_candidates_checked=0,
            warnings=[f"No local limit-up events found for {target_date.isoformat()}."],
        )

    features = repository.list_features_for_date(target_date)
    enrichments = repository.list_enrichment_for_date(target_date)
    enrichment_by_symbol = {item.symbol: item for item in enrichments}
    ratings = repository.get_live_prediction_snapshot(target_date)
    if ratings is None:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=target_date,
            first_board_repository=repository,
        )
    top_ratings = ratings.candidates[: max(top_limit, 0)]
    candidate_health: list[AgentDataHealthTopCandidate] = []

    for item in top_ratings:
        symbol = item.facts.symbol
        feature = repository.get_feature(symbol, target_date)
        enrichment = enrichment_by_symbol.get(symbol)
        candidate_health.append(
            AgentDataHealthTopCandidate(
                symbol=symbol,
                name=item.facts.name,
                score=item.score,
                rating=item.rating,
                feature_ready=feature is not None,
                enrichment_ready=bool(
                    enrichment
                    and enrichment.kline_bar_count >= 20
                    and enrichment.float_market_cap is not None
                ),
            )
        )

    first_board_features_ready = len(features) > 0
    enrichment_ready = bool(candidate_health) and all(
        item.enrichment_ready for item in candidate_health
    )
    if not first_board_features_ready:
        warnings.append("First-board features are missing for the latest local trade date.")
    if candidate_health and not enrichment_ready:
        warnings.append("Some top candidates are missing extended rating inputs.")
    outcome_completeness = build_top10_outcome_completeness(
        events=events,
        repository=repository,
        as_of_date=target_date,
        tracking_days=6,
        top_per_day=max(top_limit, 0),
    )
    if outcome_completeness.status in {"partial", "missing"}:
        warnings.extend(outcome_completeness.warnings)
    recent_dates = sorted({event.trade_date for event in events if event.trade_date <= target_date})[-5:]
    expected_dates = set(sorted({event.trade_date for event in events if event.trade_date <= target_date})[-20:])
    post_limit_symbols = sorted({
        event.symbol
        for event in events
        if event.trade_date in recent_dates and event.closed_limit
        and event.symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))
        and "ST" not in event.name.upper() and "退" not in event.name
    })
    cached_post_limit_bars = repository.list_daily_bars_for_symbols(
        post_limit_symbols, end_date=target_date
    )
    cached_by_symbol: dict[str, list] = {}
    for bar in cached_post_limit_bars:
        cached_by_symbol.setdefault(bar.symbol, []).append(bar)
    post_limit_evaluable = 0
    post_limit_source_consistent = 0
    post_limit_missing_reasons: Counter[str] = Counter()
    for symbol in post_limit_symbols:
        bars = cached_by_symbol.get(symbol, [])
        by_date = {bar.trade_date: bar for bar in bars}
        if len(expected_dates) < 20 or not expected_dates <= set(by_date):
            post_limit_missing_reasons["missing_history20"] += 1
            continue
        sources = {by_date[item].source for item in expected_dates}
        if len(sources) != 1 or not next(iter(sources), None):
            post_limit_missing_reasons["mixed_or_missing_source"] += 1
            continue
        post_limit_source_consistent += 1
        post_limit_evaluable += 1
    post_limit_coverage = (
        round(post_limit_evaluable / len(post_limit_symbols), 4)
        if post_limit_symbols else 1.0
    )
    if post_limit_coverage < 1:
        warnings.append(
            "Recent post-limit research cache coverage is "
            f"{post_limit_evaluable}/{len(post_limit_symbols)}."
        )

    status = _overall_status(
        raw_events_ready=True,
        first_board_features_ready=first_board_features_ready,
        enrichment_ready=enrichment_ready,
        outcome_status=outcome_completeness.status,
    )
    return AgentDataHealthResponse(
        trade_date=target_date,
        status=status,
        raw_events_ready=True,
        raw_event_count=len(raw_events),
        first_board_features_ready=first_board_features_ready,
        first_board_feature_count=len(features),
        enrichment_ready=enrichment_ready,
        enrichment_count=len(enrichments),
        top_candidates_checked=len(candidate_health),
        top_candidates=candidate_health,
        outcome_completeness=outcome_completeness,
        post_limit_pool_count=len(post_limit_symbols),
        post_limit_evaluable_count=post_limit_evaluable,
        post_limit_coverage_ratio=post_limit_coverage,
        post_limit_missing_history_count=post_limit_missing_reasons["missing_history20"],
        post_limit_source_consistent_count=post_limit_source_consistent,
        post_limit_pending_symbol_count=len(post_limit_symbols) - post_limit_evaluable,
        post_limit_missing_reasons=dict(sorted(post_limit_missing_reasons.items())),
        warnings=warnings,
    )


def _overall_status(
    raw_events_ready: bool,
    first_board_features_ready: bool,
    enrichment_ready: bool,
    outcome_status: str,
) -> str:
    """Return the aggregate health label."""

    if not raw_events_ready or not first_board_features_ready:
        return "missing"
    if enrichment_ready and outcome_status in {"healthy", "pending"}:
        return "healthy"
    return "partial"
