"""Timing remains observable across failed summaries, overlap and delivery."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier
from time import sleep

import httpx
import pytest

from app.models import AgentChatPerformance
from app.services.agent_timing import capture_timings, timing_snapshot, timing_stage
from app.services.llm_provider import capture_llm_usage
from app.services.session_memory import _generate_memory_draft
from test_langchain_provider import completion, provider_for


def test_failed_memory_call_keeps_purpose_duration_without_private_payload():
    secret = "private-canary-do-not-log"
    def unavailable(_):
        return httpx.Response(503, json={"error": {"message": secret}})
    with capture_timings() as timer, capture_llm_usage() as usage, provider_for(unavailable) as provider:
        with timing_stage("context"):
            _, mode, _ = _generate_memory_draft(existing=None, messages=[], llm_provider=provider)
        assert mode == "deterministic"
    report = timer.snapshot()
    calls = [span for span in report["spans"] if span["stage"] == "provider_call"]
    assert len(calls) == usage.call_count == usage.failed_call_count == 1
    assert calls[0]["purpose"] == "session_memory"
    assert calls[0]["error_type"] and calls[0]["duration_ms"] > 0
    assert usage.call_timings[0]["purpose"] == "session_memory"
    assert usage.call_timings[0]["error_type"] == calls[0]["error_type"] == "OpenAIAPIError"
    assert secret not in json.dumps([report, usage.call_timings])
    assert {"context", "session_memory", "provider_call"} <= report["stage_duration_ms"].keys()


def test_successful_provider_timing_preserves_existing_usage_accounting():
    with capture_timings() as timer, capture_llm_usage() as usage:
        with provider_for(lambda _: httpx.Response(200, json=completion())) as provider:
            with timing_stage("compliance"):
                provider.generate("private prompt", "private question")
    assert usage.call_count == usage.measured_call_count == 1
    assert usage.total_tokens == 16 and usage.failed_call_count == 0
    assert usage.call_timings[0]["purpose"] == "compliance"
    assert usage.call_timings[0]["error_type"] is None
    assert "private" not in json.dumps(timer.snapshot())


def test_parallel_tool_duration_uses_union_and_keeps_batch_parent():
    barrier = Barrier(2)
    def tool():
        with timing_stage("business_tool", tool="fixture"):
            barrier.wait(timeout=5)
            sleep(.02)
    with capture_timings() as timer:
        with timing_stage("business_tool_batch"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(copy_context().run, tool) for _ in range(2)]
                for future in futures:
                    future.result(timeout=5)
    report = timer.snapshot()
    tools = [span for span in report["spans"] if span["stage"] == "business_tool"]
    batch = next(span for span in report["spans"] if span["stage"] == "business_tool_batch")
    assert all(span["parent_id"] == batch["id"] for span in tools)
    assert max(span["start_ms"] for span in tools) < min(span["end_ms"] for span in tools)
    union = report["stage_duration_ms"]["business_tool"]
    assert union < sum(span["duration_ms"] for span in tools)
    assert union <= batch["duration_ms"] + .001
    assert report["incomplete_spans"] == 0


def test_capture_isolation_and_legacy_response_compatibility():
    assert timing_snapshot() is None
    with capture_timings() as first:
        with timing_stage("input_review"):
            with capture_timings() as nested:
                assert nested is first
                assert timing_snapshot()["incomplete_spans"] == 1
    with capture_timings() as second:
        assert second is not first and second.snapshot()["spans"] == []
    assert timing_snapshot() is None
    old = AgentChatPerformance.model_validate({"total_duration_ms": 42})
    assert old.total_duration_ms == 42 and old.timing is None


def test_span_limit_is_explicit_and_exceptions_do_not_leak_messages():
    with capture_timings() as timer:
        for _ in range(513):
            with pytest.raises(ValueError), timing_stage("control"):
                raise ValueError("secret-canary")
    report = timer.snapshot()
    assert len(report["spans"]) == 512 and report["dropped_spans"] == 1
    assert report["incomplete_spans"] == 0
    assert "secret-canary" not in json.dumps(report)
