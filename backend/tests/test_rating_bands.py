"""Rating boundaries must agree across rule scoring and final relay snapshots."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.agents import first_board
from app.models import (
    FirstBoardRatingsResponse,
    RecommendationIntelligenceItem,
    RecommendationIntelligenceResponse,
    ScoreBreakdownItem,
)
from app.services import recommendation_intelligence
from app.services.analysis import events_for_date, summarize_market
from app.services.sample_data import SAMPLE_EVENTS
from app.services.scoring_policy import build_default_scoring_policy, rating_for_score


# Regression scenario: rating bands in both scoring paths.
@pytest.mark.parametrize("score, expected", [
    (0, "D"), (49.9, "D"), (50, "C"), (50.1, "C"),
    (64.9, "C"), (65, "B"), (65.1, "B"),
    (79.9, "B"), (80, "A"), (80.1, "A"), (100, "A"),
])
def test_rating_bands_in_both_scoring_paths(monkeypatch, score, expected):
    assert rating_for_score(score) == expected
    events = events_for_date(SAMPLE_EVENTS)
    event = events[0]
    facts = first_board.build_first_board_candidate_facts(
        event, events, summarize_market(events),
    )
    policy = build_default_scoring_policy()
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    monkeypatch.setattr(first_board, "_apply_scoring_policy", lambda *_args: [
        ScoreBreakdownItem(name="test", score=score, max_score=100, evidence=[]),
    ])
    rule_rating = first_board._rate_candidate(facts, policy)
    assert rule_rating.score == score
    assert rule_rating.rating == expected

    # Use a different base score so the final path must classify the draft score.
    base_rating = rule_rating.model_copy(update={"score": 0, "rating": "D"})
    rebuilt = FirstBoardRatingsResponse(
        trade_date=event.trade_date, candidates=[base_rating], filtered_out=[],
        universe_count=len(events), generated_by=policy.version,
    )
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    monkeypatch.setattr(recommendation_intelligence, "build_first_board_ratings", lambda **_kwargs: rebuilt)
    now = datetime(2026, 5, 18, 1, tzinfo=timezone.utc)
    response = RecommendationIntelligenceResponse(
        refresh_id="test-final", refreshed_at=now, finalized_at=now,
        interval_minutes=30, stage="final", status="complete",
        relay_base_date=event.trade_date, target_trade_date=event.trade_date + timedelta(days=1),
        items=[RecommendationIntelligenceItem(
            symbol=event.symbol, name=event.name, base_trade_date=event.trade_date,
            rank=1, base_score=0, draft_score=score, refreshed_at=now,
        )],
    )
    first_repo = Mock()
    first_repo.get_live_prediction_snapshot.return_value = None
    limit_repo = Mock()
    limit_repo.list_events.return_value = events
    recommendation_intelligence._persist_final_relay_snapshot(
        response, limit_up_repository=limit_repo, first_board_repository=first_repo,
    )
    first_repo.persist_live_prediction_snapshot.assert_called_once()
    saved = first_repo.persist_live_prediction_snapshot.call_args.kwargs
    assert saved["ratings"].generated_by == policy.version
    assert saved["ratings"].candidates[0].score == score
    assert saved["ratings"].candidates[0].rating == expected
    assert saved["predictions"][0].score == score
    assert saved["predictions"][0].rating == expected
