"""Bounded request-local monotonic timings; never capture prompts or error text.

Stages may nest and tools may overlap. Per-stage totals are interval unions,
not additive across stage names. Provider spans measure logical invocations,
including SDK retries, rather than individual network attempts.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from time import perf_counter


_current = ContextVar("agent_timing", default=None)
_parent = ContextVar("agent_timing_parent", default=None)
_purpose = ContextVar("agent_model_purpose", default="unspecified")
MODEL_PURPOSES = {"session_memory", "input_review", "react_round", "compliance"}


class TimingCollector:
    def __init__(self):
        self.started = perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.spans = []
        self.dropped = 0
        self.sequence = 0
        self.active = 0
        self.lock = Lock()

    def snapshot(self):
        with self.lock:
            spans = sorted((dict(span) for span in self.spans), key=lambda span: span["id"])
            dropped = self.dropped
            active = self.active
        totals = {}
        for stage in {span["stage"] for span in spans}:
            intervals = sorted((span["start_ms"], span["end_ms"]) for span in spans if span["stage"] == stage)
            total = 0
            end = 0
            for left, right in intervals:
                total += max(0, right - max(left, end))
                end = max(end, right)
            totals[stage] = round(total, 3)
        return {"version": 1, "started_at": self.started_at,
                "elapsed_ms": round((perf_counter() - self.started) * 1000, 3),
                "stage_duration_ms": totals, "spans": spans, "dropped_spans": dropped,
                "incomplete_spans": active}


@contextmanager
def capture_timings():
    """Reuse an outer worker collector, or isolate a direct runtime invocation."""
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    collector = TimingCollector()
    token = _current.set(collector)
    parent_token = _parent.set(None)
    try:
        yield collector
    finally:
        _parent.reset(parent_token)
        _current.reset(token)


def current_model_purpose():
    return _purpose.get()


def timing_snapshot():
    collector = _current.get()
    return collector.snapshot() if collector is not None else None


@contextmanager
def timing_stage(stage, *, round=None, tool=None, operation=None):
    """Metadata must be code-owned names/counters, never user values."""
    collector = _current.get()
    purpose_token = _purpose.set(stage) if stage in MODEL_PURPOSES else None
    if collector is None:
        try:
            yield
        finally:
            if purpose_token is not None:
                _purpose.reset(purpose_token)
        return
    started = perf_counter()
    with collector.lock:
        collector.sequence += 1
        collector.active += 1
        sequence = collector.sequence
    span = {"id": sequence, "parent_id": _parent.get(), "stage": stage,
            "purpose": _purpose.get(), "start_ms": (started - collector.started) * 1000,
            "round": round, "tool": tool, "operation": operation, "error_type": None}
    parent_token = _parent.set(sequence)
    try:
        yield
    except BaseException as error:
        cause = (error.__cause__ or error) if stage == "provider_call" else error
        span["error_type"] = type(cause).__name__
        raise
    finally:
        ended = perf_counter()
        span.update(end_ms=(ended - collector.started) * 1000, duration_ms=(ended - started) * 1000)
        with collector.lock:
            collector.active -= 1
            if len(collector.spans) < 512:
                collector.spans.append(span)
            else:
                collector.dropped += 1
        _parent.reset(parent_token)
        if purpose_token is not None:
            _purpose.reset(purpose_token)


def timed_call(stage, function, *args, **kwargs):
    with timing_stage(stage):
        return function(*args, **kwargs)


def timed(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return timed_call(stage, function, *args, **kwargs)
        return wrapped
    return decorate


def timed_runtime(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        from app.models import AgentTimingReport
        with capture_timings() as collector:
            with timing_stage("runtime"):
                response = function(*args, **kwargs)
            # The worker adds context/persistence data to the final diagnostic.
            response.performance.timing = AgentTimingReport.model_validate(collector.snapshot())
            return response
    return wrapped
