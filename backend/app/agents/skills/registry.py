"""Registry and orchestration helpers for runtime Agent skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from app.agents.skills.contract import AgentSkill
from app.agents.skills.loader import load_agent_skills


class AgentSkillRegistry:
    """Resolve selected skills and enforce their minimum tool workflows."""

    def __init__(self, skills: Iterable[AgentSkill]):
        self._skills: dict[str, AgentSkill] = {}
        for skill in skills:
            if skill.name in self._skills:
                raise ValueError(f"duplicate Agent skill name: {skill.name}")
            self._skills[skill.name] = skill

    @property
    def skills(self) -> tuple[AgentSkill, ...]:
        """Return skills in stable registration order."""

        return tuple(self._skills.values())

    def get(self, name: object) -> AgentSkill | None:
        """Resolve a planner-provided skill name without accepting unknown names."""

        if not isinstance(name, str):
            return None
        normalized = name.strip().lower().replace("_", "-")
        return self._skills.get(normalized)

    def resolve(
        self,
        requested_name: object,
        tool_calls: list[dict[str, Any]],
    ) -> AgentSkill | None:
        """Prefer the explicit skill, then infer one from already selected tools."""

        selected = self.get(requested_name)
        if selected is not None:
            return selected
        selected_tools = {
            str(call.get("name") or "") for call in tool_calls if isinstance(call, dict)
        }
        for skill in self.skills:
            if any(tool.name in selected_tools for tool in skill.required_tools):
                return skill
        return None

    def resolve_from_facts(self, facts: dict[str, Any]) -> AgentSkill | None:
        """Infer the active skill after policy-repaired tools have produced facts."""

        for skill in self.skills:
            if any(tool.name in facts for tool in skill.required_tools):
                return skill
        return None

    def ensure_required_tool_calls(
        self,
        skill: AgentSkill,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prepend missing required tools while preserving planner-selected calls."""

        existing = {
            str(call.get("name") or "") for call in tool_calls if isinstance(call, dict)
        }
        required = [
            tool.to_call() for tool in skill.required_tools if tool.name not in existing
        ]
        return [*required, *tool_calls][:6]

    def schema_prompt(self) -> str:
        """Return the compact skill catalog embedded in the planner prompt."""

        return json.dumps(
            [skill.planner_payload() for skill in self.skills],
            ensure_ascii=False,
            separators=(",", ":"),
        )


AGENT_SKILL_REGISTRY = AgentSkillRegistry(
    load_agent_skills(Path(__file__).resolve().parent)
)
