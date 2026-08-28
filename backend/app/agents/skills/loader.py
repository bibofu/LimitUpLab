"""Load and validate directory-based Agent skills from ``SKILL.md`` files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.agents.skills.contract import AgentSkill, AgentSkillTool


_FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(?P<frontmatter>.*?)\r?\n---(?:\r?\n|\Z)",
    re.DOTALL,
)
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillLoadError(ValueError):
    """Raised when a ``SKILL.md`` does not satisfy the runtime contract."""


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _parse_frontmatter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_PATTERN.match(text)
    if match is None:
        raise SkillLoadError(f"{source}: missing or malformed YAML front matter")

    result: dict[str, Any] = {}
    active_section: str | None = None
    for line_number, raw_line in enumerate(
        match.group("frontmatter").splitlines(), start=2
    ):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent not in {0, 2} or ":" not in raw_line:
            raise SkillLoadError(
                f"{source}:{line_number}: unsupported front matter syntax"
            )
        key, raw_value = raw_line.strip().split(":", 1)
        key = key.strip()
        if indent == 0:
            if raw_value.strip():
                result[key] = _parse_scalar(raw_value)
                active_section = None
            else:
                result[key] = {}
                active_section = key
            continue
        if active_section is None or not isinstance(result.get(active_section), dict):
            raise SkillLoadError(
                f"{source}:{line_number}: nested value has no parent section"
            )
        result[active_section][key] = _parse_scalar(raw_value)

    body = text[match.end() :].strip()
    return result, body


def _require_string(frontmatter: dict[str, Any], key: str, source: Path) -> str:
    value = frontmatter.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillLoadError(f"{source}: '{key}' must be a non-empty string")
    return value.strip()


def _require_string_list(value: Any, key: str, source: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SkillLoadError(f"{source}: metadata '{key}' must be a non-empty list")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value):
        raise SkillLoadError(f"{source}: metadata '{key}' only accepts strings")
    return items


def load_agent_skill(path: Path) -> AgentSkill:
    """Load one validated skill from a ``SKILL.md`` path."""

    source = path.resolve()
    frontmatter, instructions = _parse_frontmatter(
        source.read_text(encoding="utf-8"), source
    )
    name = _require_string(frontmatter, "name", source)
    description = _require_string(frontmatter, "description", source)
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillLoadError(f"{source}: skill name must use lowercase hyphen-case")
    if source.parent.name != name:
        raise SkillLoadError(
            f"{source}: parent directory must match the skill name '{name}'"
        )
    if not instructions:
        raise SkillLoadError(f"{source}: skill instructions cannot be empty")

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        raise SkillLoadError(f"{source}: 'metadata' must be a mapping")
    required_tool_names = _require_string_list(
        metadata.get("required-tools"), "required-tools", source
    )
    examples = _require_string_list(metadata.get("examples"), "examples", source)
    default_arguments = metadata.get("default-tool-arguments", {})
    if not isinstance(default_arguments, dict):
        raise SkillLoadError(
            f"{source}: metadata 'default-tool-arguments' must be an object"
        )

    tools: list[AgentSkillTool] = []
    for tool_name in required_tool_names:
        arguments = default_arguments.get(tool_name, {})
        if not isinstance(arguments, dict):
            raise SkillLoadError(
                f"{source}: default arguments for '{tool_name}' must be an object"
            )
        tools.append(AgentSkillTool(name=tool_name, arguments=dict(arguments)))

    unknown_defaults = set(default_arguments) - set(required_tool_names)
    if unknown_defaults:
        names = ", ".join(sorted(unknown_defaults))
        raise SkillLoadError(
            f"{source}: defaults declared for non-required tools: {names}"
        )

    return AgentSkill(
        name=name,
        description=description,
        examples=examples,
        required_tools=tuple(tools),
        instructions=instructions,
        source_path=str(source),
    )


def load_agent_skills(root: Path) -> tuple[AgentSkill, ...]:
    """Discover every immediate child directory containing ``SKILL.md``."""

    paths = sorted(root.glob("*/SKILL.md"), key=lambda item: item.parent.name)
    if not paths:
        raise SkillLoadError(f"{root}: no directory-based Agent skills found")
    return tuple(load_agent_skill(path) for path in paths)
