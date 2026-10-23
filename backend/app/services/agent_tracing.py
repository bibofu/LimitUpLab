"""Optional Langfuse tracing for one research run.

Tracing is fail-open: disabled settings, a missing SDK, bad credentials or an
exporter failure must never change a research answer or block a worker thread.
Business code talks to this protocol only, so tests inject a no-op tracer and
the Langfuse import stays inside the adapter.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from app.config import LangfuseSettings, langfuse_settings

DEFAULT_EXPORT_TIMEOUT_SECONDS = 5
ERROR_MESSAGE_LIMIT = 500
GENERATION_NAME_LIMIT = 200


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
        payload: dict[str, Any] = {"output": output}
        if usage is not None:
            payload["usage_details"] = usage
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = _error_message(error)
        try:
            self.span.update(**payload)
        except Exception:
            return None


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
                output=output,
                metadata={"status": status},
                level="ERROR" if failed else "DEFAULT",
                status_message=_error_message(error) if error is not None else status,
            )
        except Exception:
            return None


@dataclass
class _LangfuseRun:
    client: Any
    span: Any
    trace_id: str
    finished: bool = field(default=False)

    def url(self) -> str | None:
        try:
            return self.client.get_trace_url(trace_id=self.trace_id)
        except Exception:
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
        except Exception:
            return None


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
        with ExitStack() as stack:
            try:
                trace_id = self._client.create_trace_id(seed=run_id)
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name="agent_run",
                        as_type="agent",
                        input={"message": message},
                        metadata={"run_id": run_id, **(metadata or {})},
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
            except Exception:
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
        with ExitStack() as stack:
            try:
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name=name[:GENERATION_NAME_LIMIT],
                        as_type="generation",
                        model=model,
                        model_parameters=parameters or {},
                        input=input,
                        metadata=metadata,
                    )
                )
            except Exception:
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
        with ExitStack() as stack:
            try:
                span = stack.enter_context(
                    self._client.start_as_current_observation(
                        name=name, as_type="tool", input=arguments,
                    )
                )
            except Exception:
                yield _NullTool()
                return
            handle = _LangfuseTool(span)
            try:
                yield handle
            except BaseException as error:
                handle.finish(status="error", error=error)
                raise

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            return None

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:
            return None


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
    except Exception:
        _tracer = NullTracer()
    return _tracer


def get_agent_tracer() -> AgentTracer:
    return _tracer


def shutdown_agent_tracer() -> None:
    global _tracer
    tracer = _tracer
    _tracer = NullTracer()
    tracer.shutdown()
