"""Judge worker evidence, reports and daily budget deferral."""

from datetime import datetime, timezone

from app.services.review_cases import ReviewCaseService
from app.services.review_jobs import ReviewJobService
from review_fixtures import completed_run, owner as fixture_owner
from test_review_detectors import FakeProvider, judge_json


def test_judge_worker_records_call_signal_and_report(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_SAMPLE_RATE", "0")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_SAMPLE_RATE", "0")
    owner = fixture_owner("c1")
    run_id = completed_run(owner, "review-detector-run")
    service = ReviewJobService()
    service.enqueue_recheck(run_id)
    monkeypatch.setattr(
        "app.services.review_jobs.get_review_judge_provider",
        lambda: FakeProvider(judge_json("factual_grounding")),
    )
    result = service.run_once(limit=10)
    assert result["processed_jobs"] >= 2

    cases = ReviewCaseService().list_cases()
    case = next(item for item in cases if item.run_id == run_id)
    detail = ReviewCaseService().get_case(case.case_id)
    assert detail.source == "judge"
    assert detail.severity == "high"
    assert any(signal.kind == "judge" for signal in detail.signals)
    assert {report.kind for report in detail.reports} >= {"deterministic", "judge"}
    assert service.judge_ledger.calls_today() == 1


def test_judge_budget_exhaustion_defers_the_job(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_DAILY_CALL_LIMIT", "1")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_SAMPLE_RATE", "0")
    owner = fixture_owner("c2")
    run_id = completed_run(owner, "review-budget-run")
    service = ReviewJobService()
    service.enqueue_recheck(run_id)
    jobs = [job for job in service.list_jobs() if job.kind == "judge"]
    assert jobs
    service.judge_ledger.record(
        job_id=jobs[0].job_id, run_id=run_id, status="skipped",
    )
    monkeypatch.setattr(
        "app.services.review_jobs.get_review_judge_provider",
        lambda: FakeProvider(judge_json()),
    )
    service.run_once(limit=10)
    refreshed = [
        job for job in service.list_jobs() if job.job_id == jobs[0].job_id
    ][0]
    assert refreshed.status == "pending"
    assert refreshed.available_at > datetime.now(timezone.utc)
