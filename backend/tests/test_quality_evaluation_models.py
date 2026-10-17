"""Unknown coverage and evaluator failures must never become positive labels."""

import pytest
from pydantic import ValidationError

from app.quality_evaluation_models import CheckResult, EvaluationReport, aggregate_verdict


def check(verdict):
    return CheckResult(check_id="saved_copy", dimension="persistence", verdict=verdict,
                       summary="Compare two recorded answer copies",
                       unknown_reason="The legacy copy is unavailable" if verdict == "unknown" else None)


def test_unchecked_or_partly_unknown_reports_do_not_claim_success():
    assert aggregate_verdict([]) == "unknown"
    assert aggregate_verdict([check("pass"), check("unknown")]) == "unknown"
    assert aggregate_verdict([check("fail"), check("unknown")]) == "fail"
    assert aggregate_verdict([check("pass")]) == "pass"
    with pytest.raises(ValidationError):
        CheckResult(check_id="copy", dimension="persistence", verdict="unknown", summary="Unknown")
    with pytest.raises(ValidationError):
        EvaluationReport(execution_status="succeeded", verdict="pass", checks=[check("unknown")])


@pytest.mark.parametrize("execution", ["error", "skipped"])
@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_evaluator_execution_failure_is_not_an_answer_verdict(execution, verdict):
    with pytest.raises(ValidationError):
        EvaluationReport(execution_status=execution, verdict=verdict)
