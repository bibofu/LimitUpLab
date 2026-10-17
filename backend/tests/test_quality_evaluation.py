"""Worker failure and ownership boundaries, separate from answer correctness."""

import threading

from app.quality_evaluation_models import CheckResult, EvaluationReport
from app.services.quality_evaluation import held_lease, report_signals


def test_skipped_assessment_cannot_publish_partial_findings_or_sampling_signals():
    report = EvaluationReport(execution_status="skipped", verdict="unknown", checks=[
        CheckResult(check_id="unfinished_check", dimension="test", verdict="fail", summary="Incomplete assessment"),
    ])
    # No job material should be consulted after a skipped evaluation.
    assert report_signals({}, report) == []


def test_heartbeat_failure_marks_ownership_lost_and_stops_renewals():
    attempted = threading.Event()

    class Repository:
        calls = 0

        def renew_job(self, *_args, **_kwargs):
            self.calls += 1
            attempted.set()
            raise RuntimeError("Lease no longer owned")

    repository = Repository()
    with held_lease(repository, {}, lease_seconds=0.06) as lost:
        assert attempted.wait(1)
        assert lost.wait(1)
    assert repository.calls == 1
