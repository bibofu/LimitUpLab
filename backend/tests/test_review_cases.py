"""Review case aggregation, transitions, export and session cascade."""

import time

import pytest

from app.database import connect
from app.models import AnswerFeedbackInput
from app.repositories import AnswerFeedbackRepository, ChatSessionRepository
from app.review_models import ReviewCaseUpdate
from app.services.review_cases import (
    ReviewCaseConflict,
    ReviewCaseNotExportable,
    ReviewCaseService,
    ReviewCaseValidationError,
)
from app.services.review_signals import ReviewSignalService
from review_fixtures import (
    advance_to_closed,
    completed_run,
    downvote,
    owner as fixture_owner,
)


def test_backfill_turns_existing_downvotes_into_signals_once(postgresql_database):
    owner = fixture_owner("p2j")
    run_id = completed_run(owner, "review-backfill")
    AnswerFeedbackRepository().save(
        run_id,
        owner,
        AnswerFeedbackInput(vote="down", reasons=["missing_source"], comment="缺来源"),
    )
    service = ReviewSignalService()
    assert service.backfill_downvotes(limit=10) == 1
    assert service.backfill_downvotes(limit=10) == 0
    with connect() as connection:
        rows = connection.execute(
            "SELECT severity, kind FROM answer_review_signals WHERE run_id=?",
            (run_id,),
        ).fetchall()
    assert [(row["kind"], row["severity"]) for row in rows] == [("downvote", "medium")]


def test_downvote_aggregates_one_case_with_snapshot(postgresql_database):
    owner = fixture_owner("p2a")
    run_id = completed_run(owner, "review-case-one")
    downvote(owner, run_id)

    service = ReviewCaseService()
    assert service.aggregate_signals(limit=10) == 1
    cases = service.list_cases()
    assert len(cases) == 1
    summary = cases[0]
    assert summary.status == "new"
    assert summary.source == "user_feedback"
    assert summary.severity == "high"
    assert summary.signal_count == 1
    assert summary.question_preview == "市场最高标是哪个"

    detail = service.get_case(summary.case_id)
    assert detail is not None
    assert detail.answer == "研究回答"
    assert detail.evidence["task_status"] == "complete"
    assert detail.evidence["trace_url"] == f"https://trace.example/{run_id}"
    assert detail.trace_url == f"https://trace.example/{run_id}"
    assert detail.signals[0].kind == "downvote"
    assert detail.signals[0].severity == "high"


def test_repeated_downvotes_attach_to_the_same_case(postgresql_database):
    owner = fixture_owner("p2b")
    run_id = completed_run(owner, "review-case-repeat")
    downvote(owner, run_id, ["other"], "")
    time.sleep(0.01)
    downvote(owner, run_id, ["data_error"], "数字不对")

    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    cases = service.list_cases()
    assert len(cases) == 1
    assert cases[0].signal_count == 2
    detail = service.get_case(cases[0].case_id)
    assert detail.severity == "high"
    assert [item.severity for item in detail.signals] == ["low", "high"]


def test_closed_case_keeps_status_when_a_new_signal_arrives(postgresql_database):
    owner = fixture_owner("p2c")
    run_id = completed_run(owner, "review-case-closed")
    downvote(owner, run_id, ["data_error"], "日期不对")
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case_id = service.list_cases()[0].case_id
    advance_to_closed(service, case_id)

    time.sleep(0.01)
    downvote(owner, run_id, ["incomplete"], "还是缺数据")
    service.aggregate_signals(limit=10)
    detail = service.get_case(case_id)
    assert detail.status == "closed"
    assert detail.signal_count == 2
    assert detail.has_new_signal is True
    assert detail.closed_at is not None


def test_case_updates_require_revision_and_required_fields(postgresql_database):
    owner = fixture_owner("p2d")
    run_id = completed_run(owner, "review-case-validation")
    downvote(owner, run_id)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case_id = service.list_cases()[0].case_id

    with pytest.raises(ReviewCaseValidationError, match="症状"):
        service.update_case(
            case_id,
            ReviewCaseUpdate(status="confirmed", note="确认", revision=1),
        )
    with pytest.raises(ReviewCaseValidationError, match="不允许"):
        service.update_case(
            case_id,
            ReviewCaseUpdate(status="closed", verification="通过", note="关闭", revision=1),
        )
    with pytest.raises(ReviewCaseValidationError, match="审核说明"):
        service.update_case(
            case_id,
            ReviewCaseUpdate(status="confirmed", symptom="日期错误", note="   ", revision=1),
        )

    detail = service.update_case(
        case_id,
        ReviewCaseUpdate(
            status="confirmed", symptom="日期错误", note="确认", revision=1,
        ),
    )
    assert detail.revision == 2
    assert detail.events[-1].action == "status_changed"
    with pytest.raises(ReviewCaseConflict):
        service.update_case(
            case_id,
            ReviewCaseUpdate(status="fixing", note="旧页面", revision=1),
        )


def test_export_requires_reviewed_case_and_omits_owner(postgresql_database):
    owner = fixture_owner("p2e")
    run_id = completed_run(owner, "review-case-export")
    downvote(owner, run_id)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case_id = service.list_cases()[0].case_id

    with pytest.raises(ReviewCaseNotExportable):
        service.export_markdown(case_id)

    service.update_case(
        case_id,
        ReviewCaseUpdate(
            status="confirmed", symptom="日期错误", note="确认", revision=1,
        ),
    )
    markdown = service.export_markdown(case_id)
    assert "## " in markdown
    assert "市场最高标是哪个" in markdown
    assert f"run_id={run_id}" in markdown
    assert owner not in markdown


def test_deleting_a_session_removes_cases_and_signals(postgresql_database):
    owner = fixture_owner("p2f")
    session_id = "review-case-delete"
    run_id = completed_run(owner, session_id)
    downvote(owner, run_id)
    ReviewCaseService().aggregate_signals(limit=10)

    assert ChatSessionRepository().delete_session(session_id, owner_id=owner) is True
    with connect() as connection:
        cases = connection.execute(
            "SELECT COUNT(*) AS count FROM answer_review_cases"
        ).fetchone()
        signals = connection.execute(
            "SELECT COUNT(*) AS count FROM answer_review_signals"
        ).fetchone()
    assert cases["count"] == 0
    assert signals["count"] == 0
