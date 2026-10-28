"""Optional Langfuse tracing for one research run.

Tracing is fail-open: disabled settings, a missing SDK, bad credentials or an
exporter failure must never change a research answer or block a worker thread.
Business code talks to this protocol only, so tests inject a no-op tracer and
the Langfuse import stays inside the adapter.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator, Protocol

from app.config import LangfuseSettings, langfuse_settings

DEFAULT_EXPORT_TIMEOUT_SECONDS = 5
ERROR_MESSAGE_LIMIT = 500
GENERATION_NAME_LIMIT = 200
PAYLOAD_CHAR_LIMIT = 60000
REDACTED = "[redacted]"
logger = logging.getLogger(__name__)
_trace_context = ContextVar("agent_trace_context", default={})
TRACE_FIELDS = frozenset({
    "run_id", "thread_id", "session_id", "runtime_version", "context_version", "prompt_version",
    "model_version", "recovery_count", "checkpoint_id", "resume_mode", "node", "operation",
    "memory_hit_count", "memory_tokens", "long_term_memory_tokens", "summary_triggered",
    "estimated_input_tokens", "tool_call_id", "tool_name", "receipt_reused", "tool_count",
    "tool_results", "model_rounds", "tool_choice", "streaming", "input_token_budget",
})


def _trace_failure(phase, error):
    # Exception messages can contain credentials, URLs or provider payloads.
    logger.warning("agent_tracing_failure phase=%s error_type=%s", phase, type(error).__name__)


class _TraceStack(ExitStack):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase

    def __exit__(self, *details):
        try:
            super().__exit__(*details)
        except Exception as error:
            _trace_failure(self.phase + ".close", error)
        # Exporter cleanup cannot suppress an exception from the business body.
        return False


def trace_metadata(extra=None):
    values = {**_trace_context.get(), **(extra or {})}
    return {key: value[:200] if isinstance(value, str) else value for key, value in values.items()
            if key in TRACE_FIELDS and (value is None or isinstance(value, (str, bool, int, float)))}


@contextmanager
def trace_context(**metadata):
    token = _trace_context.set(trace_metadata(metadata))
    try:
        yield
    finally:
        _trace_context.reset(token)


@contextmanager
def trace_stage(name, metadata=None):
    """Observe one execution phase without making tracing a business dependency."""
    with trace_context(**(metadata or {})), _TraceStack("stage") as stack:
        handle = _NullGeneration()
        factory = getattr(get_agent_tracer(), "stage", None)
        if factory:
            try:
                handle = stack.enter_context(factory(name=name, metadata=trace_metadata()))
            except Exception as error:
                _trace_failure("stage.enter", error)
        started = perf_counter()
        failure = None
        details = {}
        try:
            yield details
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                handle.finish(output={**trace_metadata(details), "latency_ms": round((perf_counter() - started) * 1000, 3),
                                      "status": "error" if failure else "ok"}, error=failure)
            except Exception as error:
                _trace_failure("stage.finish", error)
_SECRET_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "public_key",
    "secret",
    "secret_key",
    "token",
}
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|pk-lf-[A-Za-z0-9-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._-]{8,})"
)


class GenerationSpan(Protocol):
    """One model call; report its outcome before leaving the block."""

    def finish(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None: ...


class ToolSpan(Protocol):
    """One business tool call; status keeps ok/empty/error distinguishable."""

    def finish(
        self,
        *,
        status: str,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None: ...


class RunSpan(Protocol):
    """The root observation for one durable Agent run."""

    trace_id: str | None

    def url(self) -> str | None: ...

    def finish(
        self,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None: ...


class AgentTracer(Protocol):
    """Trace surface shared by the request, model and tool layers."""

    enabled: bool

    def run(
        self,
        *,
        run_id: str,
        session_id: str,
        owner_id: str | None = None,
        message: str = "",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[RunSpan]: ...

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any = None,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[GenerationSpan]: ...

    def tool(self, *, name: str, arguments: Any = None) -> Iterator[ToolSpan]: ...

    def stage(self, *, name: str, metadata: dict[str, Any] | None = None) -> Iterator[GenerationSpan]: ...

    def feedback_score(
        self,
        *,
        run_id: str,
        vote: str,
        reasons: list[str] | None = None,
        comment: str = "",
    ) -> bool: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class _NullGeneration:
    def finish(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None:
        return None


class _NullTool:
    def finish(
        self,
        *,
        status: str = "ok",
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        return None


@dataclass
class _NullRun:
    trace_id: str | None = None

    def url(self) -> str | None:
        return None

    def finish(
        self,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        return None


class NullTracer:
    """No-op tracer used whenever tracing is disabled or unavailable."""

    enabled = False

    @contextmanager
    def run(
        self,
        *,
        run_id: str,
        session_id: str,
        owner_id: str | None = None,
        message: str = "",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[RunSpan]:
        yield _NullRun()

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any = None,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[GenerationSpan]:
        yield _NullGeneration()

    @contextmanager
    def tool(self, *, name: str, arguments: Any = None) -> Iterator[ToolSpan]:
        yield _NullTool()

    @contextmanager
    def stage(self, *, name: str, metadata: dict[str, Any] | None = None):
        yield _NullGeneration()

    def feedback_score(
        self,
        *,
        run_id: str,
        vote: str,
        reasons: list[str] | None = None,
        comment: str = "",
    ) -> bool:
        return False

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


@dataclass
class _LangfuseGeneration:
    span: Any
    finished: bool = field(default=False)

    def finish(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        payload: dict[str, Any] = {"output": _bounded(output)}
        if usage is not None:
            payload["usage_details"] = usage
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = _error_message(error)
        try:
            self.span.update(**payload)
        except Exception as failure:
            _trace_failure("generation.finish", failure)


@dataclass
class _LangfuseTool:
    span: Any
    finished: bool = field(default=False)

    def finish(
        self,
        *,
        status: str = "ok",
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        failed = error is not None or status == "error"
        try:
            self.span.update(
                output=_bounded(output),
                metadata={**trace_metadata(), "status": status},
                level="ERROR" if failed else "DEFAULT",
                status_message=_error_message(error) if error is not None else status,
            )
        except Exception as failure:
            _trace_failure("tool.finish", failure)


@dataclass
class _LangfuseRun:
    client: Any
    span: Any
    trace_id: str
    finished: bool = field(default=False)

    def url(self) -> str | None:
        try:
            return self.client.get_trace_url(trace_id=self.trace_id)
        except Exception as error:
            _trace_failure("run.url", error)
            return None

    def finish(
        self,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        try:
            self.span.update(
                output=output,
                level="ERROR" if error is not None else "DEFAULT",
                status_message=_error_message(error) if error is not None else None,
            )
        except Exception as failure:
            _trace_failure("run.finish", failure)


class LangfuseTracer:
    """Langfuse v5 adapter with deterministic trace ids and bounded failures."""

    enabled = True

    def __init__(
        self,
        settings: LangfuseSettings,
        *,
        client: Any | None = None,
        propagate: Any | None = None,
    ) -> None:
        if client is None or propagate is None:
            from langfuse import Langfuse, propagate_attributes

            client = client or Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key,
                host=settings.host,
                environment=settings.environment,
                sample_rate=settings.sample_rate,
                timeout=DEFAULT_EXPORT_TIMEOUT_SECONDS,
                mask=mask_trace_data,
            )
            propagate = propagate or propagate_attributes
        self._client = client
        self._propagate = propagate

    @contextmanager
    def run(
        self,
        *,
        run_id: str,
        session_id: str,
        owner_id: str | None = None,
        message: str = "",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[RunSpan]:
        trace_id = ""
        with _TraceStack("run") as stack:
            try:
                trace_id = self._client.create_trace_id(seed=run_id)
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name="agent_run",
                        as_type="agent",
                        input={"message": message},
                        metadata={**trace_metadata(), "run_id": run_id, **(metadata or {})},
                        trace_context={"trace_id": trace_id},
                    )
                )
                stack.enter_context(
                    self._propagate(
                        session_id=session_id,
                        user_id=owner_id,
                        trace_name="agent_run",
                        tags=tags,
                    )
                )
            except Exception as error:
                _trace_failure("run.enter", error)
                yield _NullRun()
                return
            handle = _LangfuseRun(self._client, span, trace_id)
            try:
                yield handle
            except BaseException as error:
                handle.finish(error=error)
                raise

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any = None,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[GenerationSpan]:
        with _TraceStack("generation") as stack:
            try:
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name=name[:GENERATION_NAME_LIMIT],
                        as_type="generation",
                        model=model,
                        model_parameters=parameters or {},
                        input=_bounded(input),
                        metadata={**trace_metadata(), **(metadata or {})},
                    )
                )
            except Exception as error:
                _trace_failure("generation.enter", error)
                yield _NullGeneration()
                return
            handle = _LangfuseGeneration(span)
            try:
                yield handle
            except BaseException as error:
                handle.finish(error=error)
                raise

    @contextmanager
    def tool(self, *, name: str, arguments: Any = None) -> Iterator[ToolSpan]:
        with _TraceStack("tool") as stack:
            try:
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name=name, as_type="tool", input=_bounded(arguments), metadata=trace_metadata(),
                    )
                )
            except Exception as error:
                _trace_failure("tool.enter", error)
                yield _NullTool()
                return
            handle = _LangfuseTool(span)
            try:
                yield handle
            except BaseException as error:
                handle.finish(status="error", error=error)
                raise

    @contextmanager
    def stage(self, *, name: str, metadata: dict[str, Any] | None = None):
        with _TraceStack("stage") as stack:
            try:
                span = stack.enter_context(self._client.start_as_current_observation(
                    name=name[:GENERATION_NAME_LIMIT], as_type="span",
                    metadata={**trace_metadata(), **(metadata or {})},
                ))
            except Exception as error:
                _trace_failure("stage.enter", error)
                yield _NullGeneration()
                return
            handle = _LangfuseGeneration(span)
            try:
                yield handle
            except BaseException as error:
                handle.finish(error=error)
                raise

    def feedback_score(
        self,
        *,
        run_id: str,
        vote: str,
        reasons: list[str] | None = None,
        comment: str = "",
    ) -> bool:
        """Mirror one product vote as a Langfuse score; failures stay local."""

        try:
            trace_id = self._client.create_trace_id(seed=run_id)
            labels = ",".join(reasons or [])
            text = " ".join(part for part in (labels, comment) if part).strip()
            self._client.create_score(
                trace_id=trace_id,
                name="answer_feedback",
                value=1.0 if vote == "up" else 0.0,
                comment=text[:500] or None,
            )
            return True
        except Exception as error:
            _trace_failure("score.feedback", error)
            return False

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as error:
            _trace_failure("flush", error)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as error:
            _trace_failure("shutdown", error)


def _error_message(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:ERROR_MESSAGE_LIMIT]


def usage_details(
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
) -> dict[str, int] | None:
    """Map provider usage to Langfuse only when every count is measured."""

    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None
    return {
        "input": prompt_tokens,
        "output": completion_tokens,
        "total": total_tokens,
    }


def mask_trace_data(*, data: Any) -> Any:
    """Redact credential-shaped keys and values before export."""

    return _mask_value(data, depth=0)


def _mask_value(value: Any, *, depth: int) -> Any:
    if depth > 8:
        return "..."
    if isinstance(value, dict):
        masked: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_KEY_NAMES:
                masked[key] = REDACTED
            else:
                masked[key] = _mask_value(item, depth=depth + 1)
        return masked
    if isinstance(value, (list, tuple)):
        return [_mask_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value[:PAYLOAD_CHAR_LIMIT])
    return value


def _bounded(value: Any) -> Any:
    """Keep a payload JSON-safe and under the export size budget."""

    if value is None:
        return None
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)[:PAYLOAD_CHAR_LIMIT]
    if len(serialized) <= PAYLOAD_CHAR_LIMIT:
        return value
    return serialized[:PAYLOAD_CHAR_LIMIT] + (
        f"\n...[{len(serialized) - PAYLOAD_CHAR_LIMIT} chars truncated]"
    )


_tracer: AgentTracer = NullTracer()


def configure_agent_tracer(
    settings: LangfuseSettings | None = None,
) -> AgentTracer:
    """Build the process tracer once and keep a no-op tracer on any failure."""

    global _tracer
    resolved = settings or langfuse_settings()
    if not resolved.active:
        _tracer = NullTracer()
        return _tracer
    try:
        _tracer = LangfuseTracer(resolved)
    except Exception as error:
        _trace_failure("configure", error)
        _tracer = NullTracer()
    return _tracer


def get_agent_tracer() -> AgentTracer:
    return _tracer


def shutdown_agent_tracer() -> None:
    global _tracer
    tracer = _tracer
    _tracer = NullTracer()
    try:
        tracer.shutdown()
    except Exception as error:
        _trace_failure("shutdown", error)
