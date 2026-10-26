"""Result envelope produced by the durable Agent graph."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunnerResult:
    """One validated graph outcome awaiting atomic business delivery."""

    answer: str
    status: str
    stop_reason: str
    tool_calls: list[str] = field(default_factory=list)
    tool_messages: list[dict[str, Any]] = field(default_factory=list)
    display_blocks: list[dict[str, Any]] = field(default_factory=list)
    model_rounds: int = 0
    tool_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    compliance_reviewed: bool = False
    memory_action: dict[str, Any] | None = None
    memory_owner_revision: int | None = None
    memory_context_fingerprint: str | None = None
    memory_dependencies: list[dict[str, Any]] = field(default_factory=list)
    memory_context_used: bool = False
