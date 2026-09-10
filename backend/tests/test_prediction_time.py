import json
import sqlite3
import pytest
from contextlib import closing
from datetime import date, datetime
from types import SimpleNamespace

from app.database import initialize_database
from app.services.prediction_time import assess_prediction_time, close_provenance, provenance_errors
from app.services.prediction_time_audit import audit_prediction_times
from app.models import AgentPrediction, FirstBoardRatingsResponse, RecommendationIntelligenceResponse
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.recommendation_intelligence_repository import SQLiteRecommendationIntelligenceRepository
from app.services.evaluation_agent import select_canonical_prediction_snapshots


# Prepare the final provenance fixture or observation used by the surrounding regression scenario.
def final_provenance(created="2026-09-03T09:00:00+08:00", *, base="2026-09-02", target="2026-09-03", calendar=None):
    return {"version": "prediction-time-v1", "stage": "premarket_final",
            "target_trade_date": target, "information_cutoff_at": created,
            "calendar_verified": True, "calendar_trade_dates": calendar or [base, target]}


# Prepare the current final provenance fixture or observation used by the surrounding regression
# scenario.
def current_final_provenance(created="2026-09-03T08:00:00+08:00"):
    payload = final_provenance(created)
    payload["version"] = "prediction-time-v2"
    return payload


# Build the AgentPrediction fixture used by the surrounding regression scenario.
def stored_prediction(created="2026-09-02T16:10:00+08:00", **changes):
    data = dict(prediction_id="p", trade_date=date(2026, 9, 2), symbol="600000", name="test",
                score=80, rating="A", confidence=0.8, scoring_version="v5", prediction_source="live",
                data_as_of=date(2026, 9, 2), facts_json={}, reasons=[], risks=[],
                created_at=datetime.fromisoformat(created))
    data.update(changes)
    return AgentPrediction(**data)


# Build the SimpleNamespace fixture used by the surrounding regression scenario.
def prediction(created="2026-09-02T16:10:00+08:00", **changes):
    fields = dict(prediction_source="live", scoring_version="rule-v5",
                  trade_date=date(2026, 9, 2), data_as_of=date(2026, 9, 2),
                  created_at=datetime.fromisoformat(created), prediction_provenance={})
    fields.update(changes)
    return SimpleNamespace(**fields)


# Regression scenario: legacy close is separate from current forward final.
def test_legacy_close_is_separate_from_current_forward_final():
    verdict = assess_prediction_time(prediction())
    assert verdict.research_eligible and not verdict.strict_forward_eligible
    assert verdict.cohort == "legacy_close"
    assert "before_data_readiness_gate" in assess_prediction_time(prediction("2026-09-02T15:10:00+08:00")).reasons


# Regression scenario: legacy late final is excluded even with backdated data as of.
def test_legacy_late_final_is_excluded_even_with_backdated_data_as_of():
    assert not assess_prediction_time(prediction("2026-09-03T10:35:33+08:00")).research_eligible


# Regression scenario: close write gate requires same day after 1530.
def test_close_write_gate_requires_same_day_after_1530():
    for timestamp in ("2026-09-02T15:29:59+08:00", "2026-09-03T16:00:00+08:00"):
        assert provenance_errors(base_date=date(2026, 9, 2), data_as_of=date(2026, 9, 2),
                                 created_at=datetime.fromisoformat(timestamp), provenance=close_provenance())


# Regression scenario: audit keeps originals and marks only matching late review.
def test_audit_keeps_originals_and_marks_only_matching_late_review():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    connection.execute("""INSERT INTO agent_predictions
        (prediction_id,trade_date,symbol,name,score,rating,confidence,scoring_version,
         prediction_source,data_as_of,facts_json,reasons_json,risks_json,created_at)
        VALUES ('p','2026-09-02','600000','test',80,'A',0.8,'v5','live',
                '2026-09-03','{}','[]','[]','2026-09-03T10:35:00+08:00')""")
    originals = []
    for day, score in (("2026-09-02", 75), ("2026-09-03", 80)):
        payload = json.dumps({"reviewed_picks": [{"trade_date": "2026-09-02", "symbol": "600000",
                                               "score": score, "data_as_of": "2026-09-03"}]})
        originals.append(payload)
        connection.execute("INSERT INTO daily_review_snapshots VALUES (?,?,?,'test',?)",
                           (day, day, payload, day + "T16:00:00+08:00"))
    original_prediction = tuple(connection.execute("SELECT * FROM agent_predictions").fetchone())
    first = audit_prediction_times(connection, apply=True)
    count = connection.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0]
    audit_prediction_times(connection, apply=True)
    assert connection.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0] == count
    assert tuple(connection.execute("SELECT * FROM agent_predictions").fetchone()) == original_prediction
    assert [r[0] for r in connection.execute("SELECT report_json FROM daily_review_snapshots ORDER BY as_of_date")] == originals
    assert [r["as_of_date"] for r in first["affected_reviews"]] == ["2026-09-03"]
    connection.close()


# Regression scenario: final window uses target day and timezone.
@pytest.mark.parametrize("created,eligible", [
    ("2026-09-03T09:00:00+08:00", True),
    ("2026-09-03T01:29:59+00:00", True),
    ("2026-09-03T09:30:00+08:00", False),
    ("2026-09-03T08:59:59+08:00", False),
])
def test_final_window_uses_target_day_and_timezone(created, eligible):
    p = prediction(created, data_as_of=date(2026, 9, 3), prediction_provenance=final_provenance(created))
    assert assess_prediction_time(p).strict_forward_eligible is eligible


# Regression scenario: current final window starts at 0800.
@pytest.mark.parametrize("created,eligible", [
    ("2026-09-03T08:00:00+08:00", True),
    ("2026-09-03T07:59:59+08:00", False),
    ("2026-09-03T09:30:00+08:00", False),
])
def test_current_final_window_starts_at_0800(created, eligible):
    p = prediction(
        created,
        data_as_of=date(2026, 9, 3),
        prediction_provenance=current_final_provenance(created),
    )
    assert assess_prediction_time(p).strict_forward_eligible is eligible


# Regression scenario: exchange calendar handles weekend and rejects holiday target.
def test_exchange_calendar_handles_weekend_and_rejects_holiday_target():
    metadata = final_provenance("2026-09-07T09:00:00+08:00", base="2026-09-04", target="2026-09-07")
    p = prediction("2026-09-07T09:00:00+08:00", trade_date=date(2026, 9, 4),
                   data_as_of=date(2026, 9, 7), prediction_provenance=metadata)
    assert assess_prediction_time(p).strict_forward_eligible
    metadata["calendar_trade_dates"] = ["2026-09-04", "2026-09-08"]
    assert not assess_prediction_time(p).research_eligible


# Regression scenario: public upsert rejects backfilled or early live rows.
@pytest.mark.parametrize("timestamp", ["2026-09-02T15:29:59+08:00", "2026-09-03T16:00:00+08:00"])
def test_public_upsert_rejects_backfilled_or_early_live_rows(tmp_path, timestamp):
    repo = SQLiteFirstBoardRepository(tmp_path / "test.sqlite")
    with pytest.raises(ValueError, match="Invalid live prediction time"):
        repo.upsert_predictions([stored_prediction(timestamp)])


# Regression scenario: invalid live date cannot be filled with historical rows.
def test_invalid_live_date_cannot_be_filled_with_historical_rows():
    late = stored_prediction("2026-09-03T10:35:00+08:00")
    historical = late.model_copy(update={"prediction_id": "h", "prediction_source": "historical_backtest"})
    assert select_canonical_prediction_snapshots([late, historical]) == []


# Regression scenario: final public save rejects late metadata.
def test_final_public_save_rejects_late_metadata(tmp_path):
    response = RecommendationIntelligenceResponse(
        refresh_id="late", refreshed_at=datetime.fromisoformat("2026-09-03T10:00:00+08:00"),
        finalized_at=datetime.fromisoformat("2026-09-03T10:00:00+08:00"),
        interval_minutes=30, stage="final", status="complete", target_trade_date=date(2026, 9, 3),
        relay_base_date=date(2026, 9, 2), prediction_provenance=final_provenance("2026-09-03T10:00:00+08:00"),
    )
    with pytest.raises(ValueError, match="final_outside_preopen_window"):
        SQLiteRecommendationIntelligenceRepository(tmp_path / "test.sqlite").save_final(response)


# Regression scenario: commit window failure rolls back archive replacement and final.
def test_commit_window_failure_rolls_back_archive_replacement_and_final(tmp_path):
    repo = SQLiteFirstBoardRepository(tmp_path / "test.sqlite")
    repo.upsert_predictions([stored_prediction()])
    created = datetime.fromisoformat("2026-09-03T09:29:59+08:00")
    provenance = final_provenance(created.isoformat())
    response = RecommendationIntelligenceResponse(
        refresh_id="final", refreshed_at=created, finalized_at=created, interval_minutes=30,
        stage="final", status="complete", target_trade_date=date(2026, 9, 3),
        relay_base_date=date(2026, 9, 2), prediction_provenance=provenance,
    )
    ratings = FirstBoardRatingsResponse(trade_date=date(2026, 9, 2), candidates=[], filtered_out=[],
                                        universe_count=0, generated_by="v5", prediction_provenance=provenance)
    # Simulate the dependency failure required by this regression scenario so its error or
    # fallback path is exercised.
    def missed_window():
        raise ValueError("completed after open")
    with pytest.raises(ValueError, match="completed after open"):
        repo.persist_live_prediction_snapshot(ratings=ratings, predictions=[], top_limit=10,
            data_as_of=date(2026, 9, 3), created_at=created, replace=True,
            final_response=response, before_commit=missed_window)
    with closing(sqlite3.connect(repo.database_path)) as c:
        assert c.execute("SELECT count(*) FROM agent_predictions").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM prediction_snapshot_archive").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM recommendation_prediction_finals").fetchone()[0] == 0


# Regression scenario: final evidence must not exceed cutoff.
def test_final_evidence_must_not_exceed_cutoff():
    from app.services.prediction_time import validate_final_response
    created = datetime.fromisoformat("2026-09-03T09:00:00+08:00")
    item = SimpleNamespace(base_trade_date=date(2026, 9, 2), refreshed_at=created, facts_cutoff_at=created,
                           quote_captured_at=datetime.fromisoformat("2026-09-03T09:30:00+08:00"),
                           popularity_snapshot_at=None, latest_news=[], financial_report=None)
    response = SimpleNamespace(stage="final", relay_base_date=date(2026, 9, 2),
                               finalized_at=created, target_trade_date=date(2026, 9, 3),
                               prediction_provenance=final_provenance(), items=[item])
    with pytest.raises(ValueError, match="evidence timestamp"):
        validate_final_response(response)
