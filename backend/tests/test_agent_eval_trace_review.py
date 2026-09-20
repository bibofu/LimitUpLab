import json

from langchain_core.messages import AIMessage
import pytest

from app.agent_eval.trace_review import JUDGE_SYSTEM, review_trace
from test_agent_eval_checks import artifact, no_external_calls
from test_agent_eval_event_facts import bundle


class Judge:
    model = "test-only"

    def __init__(self, verdict="pass", broken=False):
        self.calls = 0
        self.verdict = verdict
        self.broken_attempts = 2 if broken is True else int(broken)

    def generate_messages(self, messages, tools, **kwargs):
        self.calls += 1
        assert kwargs["max_tokens"] == 1800
        packet = json.loads(messages[1].content)
        assert "evidence" in packet and "answer" in packet
        assert "react_decision" not in packet
        if self.calls <= self.broken_attempts:
            return AIMessage(content="invalid")
        dimension = {"verdict": self.verdict, "rationale": "test verdict",
                     "issue": "specific unsupported claim" if self.verdict == "fail" else "",
                     "evidence_ids": []}
        return AIMessage(content="", tool_calls=[{
            "id": "judge", "name": "submit_trace_review",
            "args": {key: dict(dimension) for key in ("task_completion", "grounding", "boundary_safety")},
        }])


def test_zero_cost_default(bundle):
    case, _, response, _ = bundle
    report = review_trace(case, response)
    assert report["judge"]["calls"] == 0
    assert report["judge"]["status"] == "disabled"
    assert report["verdict"] == "needs_review"
    assert report["efficiency"]["tool_attempt_counts"]
    assert all(d["verdict"] == "not_run" for d in report["dimensions"].values())


def test_judge_contract_forbids_fake_evidence_ids():
    assert "Assertion ID不是Evidence ID" in JUDGE_SYSTEM
    assert "evidence为空时" in JUDGE_SYSTEM


@pytest.mark.parametrize("verdict", ["pass", "fail", "needs_review"])
def test_one_call_multiple_dimensions(bundle, verdict):
    case, _, response, _ = bundle
    provider = Judge(verdict)
    report = review_trace(case, response, provider=provider, max_input_chars=100000)
    assert provider.calls == 1
    assert report["judge"]["status"] == "completed"
    assert report["dimensions"]["grounding"]["verdict"] == verdict
    assert report["verdict"] == ("fail" if verdict == "fail" else "needs_review")
    assert not report["release_eligible"]


def test_budget_skips_without_truncating(bundle):
    case, _, response, _ = bundle
    provider = Judge()
    report = review_trace(case, response, provider=provider, max_input_chars=1000)
    assert report["judge"]["status"] == "input_budget_exceeded"
    assert provider.calls == 0


def test_protocol_error_retries_once_then_stops(bundle):
    case, _, response, _ = bundle
    provider = Judge(broken=True)
    report = review_trace(case, response, provider=provider, max_input_chars=100000)
    assert report["judge"]["status"] == "judge_error"
    assert provider.calls == 2
    assert report["verdict"] == "needs_review"


def test_protocol_retry_can_recover(bundle):
    case, _, response, _ = bundle
    provider = Judge(broken=1)
    report = review_trace(case, response, provider=provider, max_input_chars=100000)
    assert report["judge"]["status"] == "completed"
    assert report["judge"]["calls"] == 2
    assert report["judge"]["protocol_retries"] == 1
    assert report["judge"]["first_error_type"] == "ValueError"


def test_invalid_trace_not_sent_to_judge(bundle):
    case, _, response, _ = bundle
    response = response.model_copy(update={"tool_results": []})
    provider = Judge()
    report = review_trace(case, response, provider=provider)
    assert report["judge"]["status"] == "invalid_trace"
    assert provider.calls == 0


def test_old_runtime_allows_budget_estimate_but_not_judgment(bundle):
    case, _, response, _ = bundle
    response = response.model_copy(deep=True)
    execution = next(t for t in response.tool_results if t.name == "react_execution")
    execution.output["version"] = "incompatible-runtime"
    provider = Judge()
    result = review_trace(case, response, provider=provider)
    assert result["judge"]["status"] == "invalid_trace" and provider.calls == 0
    assert result["judge"]["input_chars"] > 0
    assert result["judge"]["budget_decision"] in {"within_budget", "exceeded"}


def test_hard_failure_is_not_overridden(bundle):
    case, _, response, _ = bundle
    case = case.model_copy(deep=True)
    case.expected_terminal.allowed_status = ["refuse"]
    provider = Judge()
    report = review_trace(case, response, provider=provider)
    assert report["verdict"] == "fail"
    assert report["judge"]["status"] == "skipped_hard_failure"
    assert provider.calls == 0
