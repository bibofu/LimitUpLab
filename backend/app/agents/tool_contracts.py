"""Shared public contract types for Agent tools, without execution dependencies."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class AgentToolSchema:
    """Canonical public contract for one callable Agent tool."""

    name: str
    description: str
    args_schema: dict[str, Any]
    returns: str
    time_mode: Literal[
        "historical",
        "historical_or_live",
        "current",
        "latest_local",
        "latest_local_and_current",
        "retrieved_now",
    ]
    dates: tuple[str, ...] = ()
    collection: str | None = None
    notes: str = ""
    adapter: Literal["direct", "first_board_filter", "post_limit"] = "direct"

    def model_dump(self) -> dict[str, Any]:
        """Serialize the schema into a prompt-friendly dictionary."""

        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema,
            "returns": self.returns,
            "time_mode": self.time_mode,
            "dates": self.dates,
            "collection": self.collection,
            "notes": self.notes,
            "adapter": self.adapter,
        }
