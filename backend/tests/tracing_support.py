"""Recording tracer used by instrumentation tests."""

from contextlib import contextmanager


class RecordedSpan:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.finished = None

    def finish(self, **kwargs):
        self.finished = kwargs


class RecordingTracer:
    enabled = True

    def __init__(self):
        self.runs = []
        self.generations = []
        self.tools = []
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

    def flush(self):
        self.flushed = True

    def shutdown(self):
        self.shutdown_called = True
