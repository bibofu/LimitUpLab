"""Runtime contracts for reusable Agent business skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSkillTool:
    """One tool that a skill must execute before composing an answer."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_call(self) -> dict[str, Any]:
        """Return a fresh planner-compatible tool call."""

        return {"name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class AgentSkill:
    """One runtime skill loaded from a directory-based ``SKILL.md`` file."""

    name: str
    description: str
    examples: tuple[str, ...]
    required_tools: tuple[AgentSkillTool, ...]
    instructions: str
    source_path: str

    def planner_payload(self) -> dict[str, Any]:
        """Serialize the compact information needed during skill selection."""

        return {
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples),
            "required_tools": [tool.name for tool in self.required_tools],
        }

    def answer_instruction(self) -> str:
        """Return the selected skill body for progressive prompt disclosure."""

        return (
            f"\nACTIVE_SKILL: {self.name}\n"
            "The following instructions were loaded from the active SKILL.md. "
            "Follow them when composing the user-facing answer.\n"
            f"{self.instructions}\n"
        )
