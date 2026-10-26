"""429 recovery must preserve the CLI error and the whole-request budget."""

import json
from pathlib import Path
import subprocess

import pytest

from app.collectors.hithink_finance_collector import HithinkFinanceCollector, HithinkFinanceError


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class Limiter:
    def __init__(self, clock):
        self.clock = clock
        self.next_ready = clock()
        self.cooldowns = []
        self.deadlines = []

    def wait(self, deadline):
        self.deadlines.append(deadline)
        if max(self.clock(), self.next_ready) >= deadline:
            raise TimeoutError("Shared provider queue exhausted the request budget")
        self.clock.sleep(max(0, self.next_ready - self.clock()))

    def cooldown(self, seconds, *, deadline=None):
        self.cooldowns.append(seconds)
        self.next_ready = max(self.next_ready, self.clock() + seconds)


def failure(code="FUYAO_429", retry_after=None):
    result = {"ok": False, "error": {
        "code": code, "message": "Global request rate limit exceeded", "retryable": False,
    }, "meta": {"request_id": "request-429"}}
    if retry_after is not None:
        result["error"]["retry_after_seconds"] = retry_after
    return result


SUCCESS = {"ok": True, "data": {"item": []}, "meta": {"source": "remote"}}


class Runner:
    def __init__(self, clock, replies, *, stderr=False):
        self.clock, self.replies, self.stderr = clock, list(replies), stderr
        self.timeouts, self.starts, self.paths = [], [], []

    def __call__(self, command, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        self.starts.append(self.clock())
        database = Path(command[command.index("--db") + 1])
        assert database.parent.is_dir() and not database.exists()
        assert list(database.parent.iterdir()) == []
        assert command[command.index("--source") + 1] == "remote"
        self.paths.append(database)
        self.clock.now += 0.5
        reply = self.replies.pop(0)
        encoded = json.dumps(reply) if isinstance(reply, dict) else reply
        ok = isinstance(reply, dict) and reply.get("ok") is True
        stderr = "[update] A newer CLI exists.\n" + encoded + "\n[update] Check later.\n"
        return subprocess.CompletedProcess(command, 0 if ok else 1,
            stdout=encoded if ok or not self.stderr else "",
            stderr=stderr if self.stderr and not ok else "")


def setup(replies, *, timeout=15, stderr=False, with_limiter=True):
    clock = Clock()
    limiter = Limiter(clock) if with_limiter else None
    runner = Runner(clock, replies, stderr=stderr)
    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner,
        timeout_seconds=timeout, rate_limiter=limiter, clock=clock, sleep=clock.sleep,
        random_number=lambda: 0)
    return collector, runner, clock, limiter


@pytest.mark.parametrize("code", ["FUYAO_429", "429"])
def test_structured_stderr_rate_limit_recovers_despite_false_retryable(code):
    collector, runner, clock, limiter = setup([failure(code), SUCCESS], stderr=True)
    assert collector._invoke("market", "history")["ok"] is True
    assert runner.starts == [100, 102.5]
    assert runner.timeouts == [15, 12.5]
    assert limiter.deadlines == [115, 115]
    assert limiter.cooldowns == [2]
    assert all(not path.parent.exists() for path in runner.paths)


@pytest.mark.parametrize("code", ["FUYAO_4001", "4001"])
def test_cli_exhausted_4001_shares_cooldown_without_multiplying_internal_retries(code):
    collector, runner, clock, limiter = setup([failure(code), SUCCESS], stderr=True)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "history")
    assert caught.value.code == code and caught.value.retryable is True
    assert limiter.cooldowns == [2]
    assert len(runner.starts) == 1


def test_exhausted_429_has_shared_cooldown_and_request_id():
    collector, runner, clock, limiter = setup([failure(), failure(), failure()])
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("financials", "income")
    assert len(runner.starts) == 3
    assert caught.value.code == "FUYAO_429"
    assert caught.value.retryable is True
    assert caught.value.request_id == "request-429"
    assert len(limiter.cooldowns) == 3 and limiter.next_ready > clock()
    assert all(not path.parent.exists() for path in runner.paths)


def test_retry_execution_uses_remaining_budget_and_never_starts_fourth_attempt():
    collector, runner, clock, limiter = setup([failure(), failure(), SUCCESS], timeout=3)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "snapshot")
    assert caught.value.code == "FUYAO_429"
    assert runner.timeouts == [3, 0.5]
    assert clock() <= 103
    assert limiter.next_ready > clock()


def test_waiting_for_shared_slot_can_exhaust_budget_without_starting_cli():
    collector, runner, clock, limiter = setup([SUCCESS], timeout=3)
    limiter.next_ready = clock() + 4
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "snapshot")
    assert caught.value.code == "timeout" and caught.value.retryable is True
    assert runner.starts == []


@pytest.mark.parametrize("code", ["FUYAO_2003", "FUYAO_1002", "CLI_INTERNAL_ERROR"])
def test_authentication_validation_and_internal_errors_are_not_retried(code):
    collector, runner, clock, limiter = setup([failure(code), SUCCESS], stderr=True)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "snapshot")
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert caught.value.request_id == "request-429"
    assert len(runner.starts) == 1 and not limiter.cooldowns


def test_plain_stderr_mentioning_429_is_not_treated_as_structured_rate_limit():
    collector, runner, clock, limiter = setup(
        ["unstructured diagnostic with FUYAO_429 and rate limit words", SUCCESS], stderr=True)
    with pytest.raises(HithinkFinanceError):
        collector._invoke("market", "history")
    assert len(runner.starts) == 1 and not limiter.cooldowns


def test_retry_after_larger_than_budget_is_shared_but_not_slept():
    collector, runner, clock, limiter = setup([failure(retry_after=8), SUCCESS], timeout=5)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "history")
    assert caught.value.code == "FUYAO_429"
    assert caught.value.retry_after_seconds == 8
    assert limiter.cooldowns == [8]
    assert len(runner.starts) == 1 and clock() == 100.5


def test_custom_runner_without_shared_limiter_still_backs_off():
    collector, runner, clock, limiter = setup([failure(), SUCCESS], with_limiter=False)
    assert collector._invoke("market", "history")["ok"] is True
    assert runner.starts == [100, 102.5]
    assert sum(clock.sleeps) == 2


def test_retry_after_metadata_is_honored_within_budget():
    limited = failure()
    limited["meta"]["retry_after_seconds"] = 5
    collector, runner, clock, limiter = setup([limited, SUCCESS])
    assert collector._invoke("market", "history")["ok"] is True
    assert runner.starts == [100, 105.5]
    assert limiter.cooldowns == [5]


def test_cooldown_storage_failure_keeps_upstream_error_and_stops_requests():
    collector, runner, clock, limiter = setup([failure(), SUCCESS])

    def unavailable(seconds, *, deadline=None):
        assert deadline == 115
        raise TimeoutError("shared cooldown storage exceeded deadline")

    limiter.cooldown = unavailable
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "snapshot")
    assert caught.value.code == "FUYAO_429"
    assert caught.value.request_id == "request-429"
    assert caught.value.retryable is True
    assert len(runner.starts) == 1
