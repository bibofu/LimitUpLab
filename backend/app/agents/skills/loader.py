"""Load product-internal Agent Skills stored as standard SKILL.md folders."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


SKILLS_ROOT = Path(__file__).resolve().parent
DEFAULT_SKILL = "daily-review"
STOCK_SKILL = "stock-diagnosis"
STOCK_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
# Fallback words used only when a stock page or symbol is already known. They
# keep "今天市场怎么样" on daily-review while still selecting stock-diagnosis
# for "600519 最近怎么样" on a stock page.
_STOCK_QUESTION_TERMS = (
    "分析", "诊断", "怎么样", "动态", "消息", "近况", "走势", "财务", "财报",
    "基本面", "估值", "风险", "公司", "主营", "市值", "股本", "持股",
)
# Equal-length trigger matches prefer the broad daily-review playbook; a stock
# playbook still wins when its trigger is longer ("这只股" over "复盘").
_SKILL_PRIORITY = {DEFAULT_SKILL: 1}
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


def _trigger_match_length(skill: Skill, message: str) -> int:
    folded = message.casefold()
    return max(
        (len(trigger.casefold()) for trigger in skill.triggers
         if trigger.casefold() in folded),
        default=0,
    )


def _has_stock_context(request: Any | None, message: str) -> bool:
    symbol = str(getattr(request, "symbol", "") or "").strip()
    page = getattr(request, "page_context", None)
    page_name = str(page.get("page") or "").casefold() if isinstance(page, dict) else ""
    return bool(symbol) or page_name == "stock_detail" or bool(STOCK_CODE.search(message))


def _looks_like_stock_question(message: str) -> bool:
    folded = message.casefold()
    return any(term in folded for term in _STOCK_QUESTION_TERMS)


def resolve_agent_skill(request: Any | None) -> str | None:
    """Return the most specific matching playbook body.

    Longest trigger wins; on a tie daily-review wins so a market review that
    happens to mention "个股" is not turned into a single-stock diagnosis.
    When no trigger matches but the request already carries a stock page or
    symbol, stock-diagnosis is used only for stock-shaped questions so a
    market question on a stock page still stays on daily-review.
    """

    message = str(getattr(request, "message", "") or "")
    if not message:
        return None
    matches = []
    for skill in available_skills():
        length = _trigger_match_length(skill, message)
        if length:
            matches.append((length, _SKILL_PRIORITY.get(skill.name, 0), skill.name, skill))
    if matches:
        matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return matches[0][3].body
    if _has_stock_context(request, message) and _looks_like_stock_question(message):
        try:
            return load_skill(STOCK_SKILL).body
        except SkillError:
            return None
    return None


def clear_skill_cache() -> None:
    """Test hook; prompts are cached for the process lifetime in production."""

    load_skill.cache_clear()
