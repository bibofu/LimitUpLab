"""Health checks for Agent data dependencies."""

from datetime import date

from app.agents.first_board import build_first_board_ratings
from app.models import (
    AgentDataHealthResponse,
    AgentDataHealthTopCandidate,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import latest_trade_date


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

    status = _overall_status(
        raw_events_ready=True,
        first_board_features_ready=first_board_features_ready,
        enrichment_ready=enrichment_ready,
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
        warnings=warnings,
    )


def _overall_status(
    raw_events_ready: bool,
    first_board_features_ready: bool,
    enrichment_ready: bool,
) -> str:
    """Return the aggregate health label."""

    if not raw_events_ready or not first_board_features_ready:
        return "missing"
    if enrichment_ready:
        return "healthy"
    return "partial"
