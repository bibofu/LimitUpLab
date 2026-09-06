import json
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

from app.database import initialize_database
from app.services.prediction_time import assess_prediction_time, close_provenance, provenance_errors
from app.services.prediction_time_audit import audit_prediction_times


def prediction(created="2026-09-02T16:10:00+08:00", **changes):
    fields = dict(prediction_source="live", scoring_version="rule-v5",
                  trade_date=date(2026, 9, 2), data_as_of=date(2026, 9, 2),
                  created_at=datetime.fromisoformat(created), prediction_provenance={})
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_legacy_close_is_separate_from_current_forward_final():
    verdict = assess_prediction_time(prediction())
    assert verdict.research_eligible and not verdict.strict_forward_eligible
    assert verdict.cohort == "legacy_close"
    assert "before_data_readiness_gate" in assess_prediction_time(prediction("2026-09-02T15:10:00+08:00")).reasons


def test_legacy_late_final_is_excluded_even_with_backdated_data_as_of():
    assert not assess_prediction_time(prediction("2026-09-03T10:35:33+08:00")).research_eligible


def test_close_write_gate_requires_same_day_after_1530():
    for timestamp in ("2026-09-02T15:29:59+08:00", "2026-09-03T16:00:00+08:00"):
        assert provenance_errors(base_date=date(2026, 9, 2), data_as_of=date(2026, 9, 2),
                                 created_at=datetime.fromisoformat(timestamp), provenance=close_provenance())


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
