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
from app.services.similar_cases import find_similar_first_board_cases


def build_agent_data_health(
    events: list[LimitUpEvent],
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    trade_date: date | None = None,
    top_limit: int = 5,
    similar_limit: int = 5,
) -> AgentDataHealthResponse:
    """Build health status for raw events, features and similar-case caches."""

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
            similar_cases_ready=False,
            post_bars_ready=False,
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
            similar_cases_ready=False,
            post_bars_ready=False,
            warnings=[f"No local limit-up events found for {target_date.isoformat()}."],
        )

    features = repository.list_features_for_date(target_date)
    ratings = build_first_board_ratings(events=events, trade_date=target_date)
    top_ratings = ratings.candidates[: max(top_limit, 0)]
    candidate_health: list[AgentDataHealthTopCandidate] = []

    for item in top_ratings:
        symbol = item.facts.symbol
        feature = repository.get_feature(symbol, target_date)
        similar_case_count = 0
        post_bar_cases = 0
        if feature is not None:
            try:
                response = find_similar_first_board_cases(
                    symbol=symbol,
                    trade_date=target_date,
                    repository=repository,
                    limit=similar_limit,
                )
                similar_case_count = len(response.cases)
                post_bar_cases = sum(1 for case in response.cases if case.post_bars)
            except ValueError as error:
                warnings.append(f"{symbol}: {error}")
        candidate_health.append(
            AgentDataHealthTopCandidate(
                symbol=symbol,
                name=item.facts.name,
                score=item.score,
                rating=item.rating,
                feature_ready=feature is not None,
                similar_case_count=similar_case_count,
                similar_cases_with_post_bars=post_bar_cases,
            )
        )

    first_board_features_ready = len(features) > 0
    similar_cases_ready = bool(candidate_health) and all(
        item.similar_case_count > 0 for item in candidate_health
    )
    post_bars_ready = bool(candidate_health) and all(
        item.similar_cases_with_post_bars > 0 for item in candidate_health
    )
    if not first_board_features_ready:
        warnings.append("First-board features are missing for the latest local trade date.")
    if candidate_health and not similar_cases_ready:
        warnings.append("Some top candidates cannot retrieve historical similar cases.")
    if candidate_health and not post_bars_ready:
        warnings.append("Some top candidates have similar cases without post-board bars.")

    status = _overall_status(
        raw_events_ready=True,
        first_board_features_ready=first_board_features_ready,
        similar_cases_ready=similar_cases_ready,
        post_bars_ready=post_bars_ready,
    )
    return AgentDataHealthResponse(
        trade_date=target_date,
        status=status,
        raw_events_ready=True,
        raw_event_count=len(raw_events),
        first_board_features_ready=first_board_features_ready,
        first_board_feature_count=len(features),
        top_candidates_checked=len(candidate_health),
        similar_cases_ready=similar_cases_ready,
        post_bars_ready=post_bars_ready,
        top_candidates=candidate_health,
        warnings=warnings,
    )


def _overall_status(
    raw_events_ready: bool,
    first_board_features_ready: bool,
    similar_cases_ready: bool,
    post_bars_ready: bool,
) -> str:
    """Return the aggregate health label."""

    if not raw_events_ready or not first_board_features_ready:
        return "missing"
    if similar_cases_ready and post_bars_ready:
        return "healthy"
    return "partial"
