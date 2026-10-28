"""Retention deletes aged terminal review data but keeps open cases."""

from datetime import datetime, timedelta, timezone

from app.database import connect
from app.services.review_cases import ReviewCaseService
from app.services.review_jobs import ReviewJobService
from review_fixtures import (
    advance_to_closed,
    completed_run,
    downvote,
    owner as fixture_owner,
)


OLD = datetime.now(timezone.utc) - timedelta(days=200)


def _age_run(run_id: str) -> None:
    with connect() as db:
        db.execute(
            "UPDATE answer_feedback SET updated_at=? WHERE run_id=?",
            (OLD, run_id),
        )
        db.execute(
            "UPDATE answer_review_signals SET created_at=? WHERE run_id=?",
            (OLD, run_id),
        )
        db.execute(
            """
            INSERT INTO answer_review_jobs (
                job_id, run_id, kind, priority, status, detector_version,
                config_fingerprint, created_at, finished_at
            )
            VALUES (?, ?, 'deterministic', 100, 'succeeded', 'deterministic-v1',
                    'retention-test', ?, ?)
            """,
            (f"job_old_{run_id}", run_id, OLD, OLD),
        )


def _age_case(case_id: str) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE answer_review_cases
            SET created_at=?, updated_at=?, reviewed_at=?, closed_at=?
            WHERE case_id=?
            """,
            (OLD, OLD, OLD, OLD, case_id),
        )


def test_retention_deletes_old_terminal_cases_and_keeps_open_ones(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_RETENTION_DAYS", "180")
    owner = fixture_owner("e1")
    closed_run = completed_run(owner, "retention-closed")
    downvote(owner, closed_run)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    closed_case = service.list_cases()[0]
    advance_to_closed(service, closed_case.case_id)
    _age_case(closed_case.case_id)
    _age_run(closed_run)

    open_run = completed_run(owner, "retention-open")
    downvote(owner, open_run)
    service.aggregate_signals(limit=10)
    open_case = next(item for item in service.list_cases() if item.run_id == open_run)
    _age_case(open_case.case_id)
    _age_run(open_run)

    result = ReviewJobService().cleanup_retention()
    assert result["cases"] == 1
    assert result["signals"] == 1
    assert result["jobs"] == 2
    assert result["feedback"] == 2

    with connect() as db:
        cases = db.execute(
            "SELECT case_id FROM answer_review_cases ORDER BY case_id"
        ).fetchall()
        signals = db.execute(
            "SELECT run_id FROM answer_review_signals ORDER BY run_id"
        ).fetchall()
    assert [row["case_id"] for row in cases] == [open_case.case_id]
    assert {row["run_id"] for row in signals} == {open_run}
