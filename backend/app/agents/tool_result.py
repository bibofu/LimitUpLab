"""Structured tool results and their persisted trace representation."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.models import AgentToolOutcome, AgentToolTrace


@dataclass(frozen=True)
class ToolResult:
    """Internal tool result with full output and compact trace."""

    name: str
    input: dict[str, Any]
    output: Any
    summary: str
    status: str = "success"
    trace_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    result_status: Literal["ok", "empty", "partial", "error"] | None = None
    data_fresh: bool | None = None
    source_errors: tuple[str, ...] = ()

    def trace(self) -> AgentToolTrace:
        """Return the compact trace sent to frontend and saved in runs."""

        result = None
        if self.result_status is not None:
            result = AgentToolOutcome(
                status=self.result_status,
                data_fresh=self.data_fresh,
                source_errors=list(self.source_errors),
                payload=self.trace_output,
            )
        return AgentToolTrace(
            name=self.name,
            input=self.input,
            summary=self.summary,
            status=self.status,  # type: ignore[arg-type]
            output=self.trace_output,
            error=self.error,
            result=result,
        )
