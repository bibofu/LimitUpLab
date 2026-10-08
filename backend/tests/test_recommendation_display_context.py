"""History fallback must preserve both candidate dates and the latest empty state."""

from datetime import date, datetime
import sqlite3

import pytest

from app.models import RecommendationIntelligenceItem, RecommendationIntelligenceResponse
from app.repositories.recommendation_intelligence_repository import (
    SQLiteRecommendationIntelligenceRepository,
)


def test_history_fallback_exposes_latest_missed_target_without_rewriting_snapshot(tmp_path):
    path = tmp_path / "recommendations.sqlite"
    repository = SQLiteRecommendationIntelligenceRepository(path)
    old = RecommendationIntelligenceResponse(
        refresh_id="old-draft", interval_minutes=1440, status="complete",
        refreshed_at=datetime.fromisoformat("2026-09-29T16:00:00+08:00"),
        relay_base_date=date(2026, 9, 29), target_trade_date=date(2026, 9, 30),
        items=[RecommendationIntelligenceItem(
            strategy="relay", base_trade_date=date(2026, 9, 29), symbol="600001",
            name="测试", rank=1, base_score=80,
            refreshed_at=datetime.fromisoformat("2026-09-29T16:00:00+08:00"),
        )],
    )
    repository.save(old)
    missed = RecommendationIntelligenceResponse(
        refresh_id="missed", interval_minutes=1440, status="partial",
        stage="missed_cutoff",
        refreshed_at=datetime.fromisoformat("2026-10-08T11:00:00+08:00"),
        relay_base_date=date(2026, 9, 30), target_trade_date=date(2026, 10, 8),
        warnings=["盘前固化窗口已错过"],
    )
    repository.save(missed)
    with sqlite3.connect(path) as connection:
        original = connection.execute(
            "SELECT response_json FROM recommendation_intelligence_snapshots"
        ).fetchone()[0]

    displayed = repository.get_latest_displayable()

    assert displayed.target_trade_date == date(2026, 9, 30)
    assert displayed.relay_base_date == date(2026, 9, 29)
    assert displayed.refreshed_at == old.refreshed_at
    assert displayed.items == old.items
    context = displayed.display_context
    assert context.is_history_fallback is True
    assert context.latest_target_trade_date == date(2026, 10, 8)
    assert context.latest_stage == "missed_cutoff"
    assert context.latest_refreshed_at == missed.refreshed_at
    assert context.latest_warnings == missed.warnings
    assert repository.get_latest() == missed
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT response_json FROM recommendation_intelligence_snapshots"
        ).fetchone()[0] == original


def test_current_empty_response_has_no_fabricated_history(tmp_path):
    repository = SQLiteRecommendationIntelligenceRepository(tmp_path / "empty.sqlite")
    current = RecommendationIntelligenceResponse(
        refresh_id="empty", interval_minutes=1440, status="partial",
        stage="missed_cutoff", target_trade_date=date(2026, 10, 8),
        refreshed_at=datetime.fromisoformat("2026-10-08T11:00:00+08:00"),
    )
    repository.save(current)
    assert repository.get_latest_displayable() == current


@pytest.mark.parametrize("current_stage", ["draft", "final", "missed_cutoff"])
def test_final_history_preserves_current_stage_and_display_context_is_read_only(tmp_path, current_stage):
    path = tmp_path / "final-history.sqlite"
    repository = SQLiteRecommendationIntelligenceRepository(path)
    current = RecommendationIntelligenceResponse(
        refresh_id="empty-current", interval_minutes=1440, status="partial",
        stage=current_stage, target_trade_date=date(2026, 10, 8),
        refreshed_at=datetime.fromisoformat("2026-10-08T11:00:00+08:00"),
    )
    repository.save(current)
    # Read fixture: the immutable final already exists independently of current.
    final = current.model_copy(update={
        "refresh_id": "historical-final", "stage": "final",
        "target_trade_date": date(2026, 9, 30),
        "relay_base_date": date(2026, 9, 29),
        "refreshed_at": datetime.fromisoformat("2026-09-30T08:00:00+08:00"),
        "finalized_at": datetime.fromisoformat("2026-09-30T08:00:00+08:00"),
        "items": [RecommendationIntelligenceItem(
            strategy="relay", base_trade_date=date(2026, 9, 29), symbol="600001",
            name="测试", rank=1, base_score=80,
            refreshed_at=datetime.fromisoformat("2026-09-30T08:00:00+08:00"),
        )],
    })
    raw = final.model_dump_json()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO recommendation_prediction_finals "
            "(target_trade_date, finalized_at, response_json) VALUES (?, ?, ?)",
            ("2026-09-30", "2026-09-30T08:00:00+08:00", raw),
        )

    displayed = repository.get_latest_displayable()
    assert displayed.stage == "final"
    assert displayed.target_trade_date == date(2026, 9, 30)
    assert displayed.display_context.latest_stage == current_stage
    assert displayed.display_context.latest_target_trade_date == date(2026, 10, 8)
    assert repository.get_final("2026-09-30") == final
    repository.save(displayed)
    assert repository.get_latest().display_context is None
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT response_json FROM recommendation_prediction_finals"
        ).fetchone()[0] == raw
