"""Sampling determinism, daily caps and the review worker loop."""

from app.services.review_cases import ReviewCaseService
from app.services.review_jobs import ReviewJobService, sample_bucket
from review_fixtures import completed_run, owner as fixture_owner


def test_manual_sample_worker_creates_a_low_priority_case(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_SAMPLE_RATE", "0")
    owner = fixture_owner("a1")
    completed_run(owner, "review-sample-manual")

    service = ReviewJobService()
    assert service.enqueue_manual_sample(limit=5) == 1
    assert service.enqueue_manual_sample(limit=5) == 0
    result = service.run_once(limit=5)
    assert result["processed_jobs"] == 1
    assert result["aggregated_signals"] == 1

    cases = ReviewCaseService().list_cases()
    assert len(cases) == 1
    assert cases[0].source == "sample"
    assert cases[0].severity == "low"
    jobs = service.list_jobs()
    assert jobs[0].status == "succeeded"


def test_sampling_is_stable_and_respects_the_daily_limit(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_SAMPLE_RATE", "1")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_SAMPLE_DAILY_LIMIT", "2")
    owner = fixture_owner("a2")
    run_ids = [
        completed_run(owner, f"review-sample-{index}") for index in range(3)
    ]
    assert sample_bucket(run_ids[0]) == sample_bucket(run_ids[0])
    assert sample_bucket(run_ids[0]) != sample_bucket(run_ids[1])

    service = ReviewJobService()
    assert service.sample_recent_runs(limit=10) == 2
    assert service.sample_recent_runs(limit=10) == 0


def test_review_disabled_skips_sampling_and_jobs(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_REVIEW_ENABLED", "false")
    owner = fixture_owner("a3")
    completed_run(owner, "review-sample-disabled")
    service = ReviewJobService()
    assert service.enqueue_manual_sample(limit=5) == 1
    assert service.run_once(limit=5) == {
        "sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0,
    }
