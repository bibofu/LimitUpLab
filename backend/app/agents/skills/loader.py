"""Load product-internal Agent Skills stored as standard SKILL.md folders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


SKILLS_ROOT = Path(__file__).resolve().parent
DEFAULT_SKILL = "daily-review"
logger = logging.getLogger(__name__)


class SkillError(ValueError):
    """A skill folder does not satisfy the minimal SKILL.md contract."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    triggers: tuple[str, ...]
    path: Path


def _parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillError(f"{path.name}: missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise SkillError(f"{path.name}: unterminated YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as error:
        raise SkillError(f"{path.name}: invalid YAML frontmatter") from error
    if not isinstance(frontmatter, dict):
        raise SkillError(f"{path.name}: frontmatter must be a mapping")
    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    body = parts[2].strip()
    if not name or not description or not body:
        raise SkillError(f"{path.name}: name, description and body are required")
    metadata = frontmatter.get("metadata")
    triggers = metadata.get("triggers") if isinstance(metadata, dict) else None
    if triggers is None:
        triggers = frontmatter.get("triggers")
    if not isinstance(triggers, list) or not all(isinstance(item, str) for item in triggers):
        raise SkillError(f"{path.name}: metadata.triggers must be a string list")
    if path.parent.name != name:
        raise SkillError(f"{path.name}: folder name must match the skill name")
    return Skill(name=name, description=description, body=body,
                 triggers=tuple(item.strip() for item in triggers if item.strip()),
                 path=path)


@lru_cache(maxsize=None)
def load_skill(name: str = DEFAULT_SKILL) -> Skill:
    """Read one skill from ``<skills>/<name>/SKILL.md`` and cache it."""

    path = SKILLS_ROOT / name / "SKILL.md"
    if not path.is_file():
        raise SkillError(f"Skill not found: {path}")
    return _parse_skill(path)


def available_skills() -> tuple[Skill, ...]:
    """Discover every folder that contains a SKILL.md, sorted by name."""

    skills = []
    for candidate in sorted(SKILLS_ROOT.iterdir() if SKILLS_ROOT.is_dir() else []):
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            try:
                skills.append(load_skill(candidate.name))
            except SkillError as error:
                logger.warning("skill_load_failed", extra={"skill": candidate.name, "error": type(error).__name__})
    return tuple(skills)


def matches_skill(name: str, text: Any) -> bool:
    """Return whether the user's own wording should load this skill."""

    message = str(text or "").casefold()
    if not message:
        return False
    try:
        skill = load_skill(name)
    except Exception as error:  # noqa: BLE001 - a broken skill never blocks research
        logger.warning("skill_match_failed", extra={"skill": name, "error": type(error).__name__})
        return False
    return any(trigger.casefold() in message for trigger in skill.triggers)


def resolve_agent_skill(request: Any | None) -> str | None:
    """Return the playbook body when the request matches a known skill."""

    message = str(getattr(request, "message", "") or "")
    if matches_skill(DEFAULT_SKILL, message):
        try:
            return load_skill(DEFAULT_SKILL).body
        except SkillError:
            return None
    return None


def clear_skill_cache() -> None:
    """Test hook; prompts are cached for the process lifetime in production."""

    load_skill.cache_clear()
