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
    """Declarative workflow and response contract for a question family."""

    name: str
    description: str
    examples: tuple[str, ...]
    required_tools: tuple[AgentSkillTool, ...]
    answer_rules: tuple[str, ...]

    def planner_payload(self) -> dict[str, Any]:
        """Serialize the compact information needed during skill selection."""

        return {
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples),
            "required_tools": [tool.name for tool in self.required_tools],
        }

    def answer_instruction(self) -> str:
        """Build the active-skill instruction injected into answer generation."""

        rules = " ".join(
            f"{index}. {rule}" for index, rule in enumerate(self.answer_rules, start=1)
        )
        return f" ACTIVE_SKILL: {self.name}. RESPONSE_CONTRACT: {rules}"
