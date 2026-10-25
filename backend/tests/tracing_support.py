"""Recording tracer used by instrumentation tests."""

from contextlib import contextmanager
from threading import Event


class RecordedSpan:
    trace_id = None

    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.finished = None
        self.finished_event = Event()

    def finish(self, **kwargs):
        self.finished = kwargs
        self.finished_event.set()

    def url(self):
        return None


class RecordingTracer:
    enabled = True

    def __init__(self):
        self.runs = []
        self.generations = []
        self.tools = []
        self.stages = []
        self.flushed = False
        self.shutdown_called = False

    @contextmanager
    def run(self, **kwargs):
        span = RecordedSpan(kwargs)
        self.runs.append(span)
        yield span

    @contextmanager
    def generation(self, **kwargs):
        span = RecordedSpan(kwargs)
        self.generations.append(span)
        yield span

    @contextmanager
    def tool(self, **kwargs):
        span = RecordedSpan(kwargs)
        self.tools.append(span)
        yield span

    @contextmanager
    def stage(self, **kwargs):
        span = RecordedSpan(kwargs)
        self.stages.append(span)
        yield span

    def flush(self):
        self.flushed = True

    def shutdown(self):
        self.shutdown_called = True
