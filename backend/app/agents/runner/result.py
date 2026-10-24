"""Result envelope for the lightweight runner."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunnerResult:
    """One shadow-run answer plus the facts needed to compare it with the legacy runtime."""

    answer: str
    status: str
    stop_reason: str
    tool_calls: list[str] = field(default_factory=list)
    tool_messages: list[dict[str, Any]] = field(default_factory=list)
    model_rounds: int = 0
    tool_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    compliance_reviewed: bool = False
