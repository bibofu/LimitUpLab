"""Request-local execution state shared by ordered tool handlers."""

from dataclasses import dataclass, field
from typing import Any

from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import AgentChatRequest, AgentToolTrace, FirstBoardRatingsResponse


@dataclass
class ExecutionState:
    tools: AgentToolRegistry
    request: AgentChatRequest
    context_symbol: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    traces: list[AgentToolTrace] = field(default_factory=list)
    call_names: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    latest_ratings: FirstBoardRatingsResponse | None = None
    latest_ratings_tool: ToolResult | None = None
