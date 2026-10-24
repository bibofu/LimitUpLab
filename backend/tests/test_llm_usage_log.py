"""Per-call usage diagnostics: context, cache hits and opt-in logging."""

from __future__ import annotations

import json

from app.services.llm_provider import LLMResult, LLMUsageTracker
from app.services.llm_usage_log import LLMCallUsage, log_llm_call


def result(**overrides) -> LLMResult:
    payload = {
        "content": "",
        "model": "model-a",
        "provider": "test",
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "total_tokens": 1050,
        "cached_tokens": 800,
        "duration_ms": 12,
    }
    payload.update(overrides)
    return LLMResult(**payload)


def test_tracker_keeps_per_call_context_and_cached_tokens():
    tracker = LLMUsageTracker()
    tracker.set_context(stage="agent", round=1, tool_definition_chars=33530)
    tracker.begin_call("model-a")
    tracker.complete_call(result())

    tracker.set_context(stage="agent", round=2)
    tracker.begin_call("model-a")
    tracker.complete_call(result(prompt_tokens=None, completion_tokens=None, total_tokens=None))

    assert len(tracker.calls) == 2
    assert tracker.calls[0].context == {
        "stage": "agent", "round": 1, "tool_definition_chars": 33530,
    }
    assert tracker.calls[0].cached_tokens == 800
    assert tracker.calls[0].status == "complete"
    assert tracker.calls[1].context == {"stage": "agent", "round": 2}
    assert tracker.calls[1].prompt_tokens is None
    assert tracker.measured_call_count == 1
    assert tracker.call_count == 2


def test_context_is_consumed_by_the_next_call_only():
    tracker = LLMUsageTracker()
    tracker.set_context(stage="compliance_review")
    tracker.begin_call("model-a")
    tracker.begin_call("model-a")

    assert tracker.calls[0].context == {"stage": "compliance_review"}
    assert tracker.calls[1].context == {}


def test_failed_call_keeps_its_record():
    tracker = LLMUsageTracker()
    tracker.set_context(stage="agent", round=1)
    tracker.begin_call("model-a")
    tracker.fail_call()

    assert tracker.failed_call_count == 1
    assert tracker.calls[0].status == "failed"
    assert tracker.calls[0].context == {"stage": "agent", "round": 1}


def test_logging_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_LLM_USAGE_LOG", raising=False)
    log_llm_call("scope", LLMCallUsage(model="model-a"))

    assert list(tmp_path.iterdir()) == []


def test_log_writes_one_json_line_per_call(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "calls.jsonl"
    monkeypatch.setenv("LIMITUPLAB_LLM_USAGE_LOG", str(path))
    tracker = LLMUsageTracker()
    tracker.set_context(stage="agent", round=1, tool_definition_chars=33530)
    tracker.begin_call("model-a")
    tracker.complete_call(result())

    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["scope_id"] == tracker.scope_id
    assert line["model"] == "model-a"
    assert line["status"] == "complete"
    assert line["prompt_tokens"] == 1000
    assert line["cached_tokens"] == 800
    assert line["cache_hit_ratio"] == 0.8
    assert line["context"] == {
        "stage": "agent", "round": 1, "tool_definition_chars": 33530,
    }


def test_unwritable_log_never_breaks_the_request(tmp_path, monkeypatch):
    directory = tmp_path / "as-directory"
    directory.mkdir()
    monkeypatch.setenv("LIMITUPLAB_LLM_USAGE_LOG", str(directory))

    log_llm_call("scope", LLMCallUsage(model="model-a"))
