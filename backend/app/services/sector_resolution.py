"""Deterministic sector-name resolution over the persisted catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# A broad name is a group of same-level industry indexes, not an arbitrary pick.
SECTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "汽车": ("汽车整车", "汽车零部件", "汽车服务及其他"),
    "军工": ("军工装备", "军工电子"),
    "国防军工": ("军工装备", "军工电子"),
}
CONCEPT_ALIASES = {
    "ai": "人工智能",
    "aigc": "AIGC概念",
    "新能源车": "新能源汽车",
}
_NORMALIZE_TERMS = (
    "今天", "今日", "最近", "近期", "a股", "板块", "行业", "概念", "相关",
    "走势", "行情", "表现", "怎么样", "如何",
)


class SectorNotFound(ValueError):
    """The query does not name an available industry or concept."""


@dataclass(frozen=True)
class SectorMatch:
    thscode: str
    name: str
    category: str
    in_summary: bool = False


@dataclass(frozen=True)
class SectorResolution:
    requested: str
    members: tuple[SectorMatch, ...]
    group: bool = False
    alternatives: tuple[SectorMatch, ...] = ()

    @property
    def label(self) -> str:
        if self.group:
            return normalize_sector_name(self.requested) or self.requested.strip()
        return self.members[0].name

    @property
    def category(self) -> str:
        return self.members[0].category

    @property
    def thscodes(self) -> tuple[str, ...]:
        return tuple(item.thscode for item in self.members)


def normalize_sector_name(value: str) -> str:
    normalized = "".join(str(value or "").lower().split())
    for term in _NORMALIZE_TERMS:
        normalized = normalized.replace(term, "")
    return normalized


def resolve_sector(
    catalog: list[dict[str, Any]], query: str, *, category: str | None = None,
) -> SectorResolution:
    """Resolve exact names first, then a group alias, then unique containment."""

    requested = str(query or "").strip()
    normalized = normalize_sector_name(requested)
    if not normalized:
        raise SectorNotFound("板块名不能为空")
    normalized = CONCEPT_ALIASES.get(normalized, normalized)

    def matches(source: list[dict[str, Any]]) -> list[SectorMatch]:
        return [
            SectorMatch(item["thscode"], item["name"], item["category"],
                        bool(item.get("in_summary")))
            for item in source
        ]

    pool = matches(catalog)
    if category:
        pool = [item for item in pool if item.category == category]

    group = SECTOR_GROUPS.get(normalized)
    if group and category in {None, "industry"}:
        members = _members_by_name(pool, group)
        if len(members) == len(group) and all(item.category == "industry" for item in members):
            related = [
                item for item in matches(catalog)
                if item not in members and (
                    normalized in normalize_sector_name(item.name)
                    or normalize_sector_name(item.name) in normalized
                )
            ]
            return SectorResolution(requested, tuple(members), group=True,
                                    alternatives=tuple(related))

    exact = [item for item in pool if normalize_sector_name(item.name) == normalized]
    if exact:
        chosen = _prefer(exact)
        return SectorResolution(
            requested, (chosen,), alternatives=tuple(item for item in exact if item != chosen),
        )

    contains = [
        item for item in pool
        if normalized in normalize_sector_name(item.name)
        or normalize_sector_name(item.name) in normalized
    ]
    if contains:
        chosen = _prefer(contains)
        alternatives = tuple(item for item in contains if item != chosen)
        return SectorResolution(requested, (chosen,), alternatives=alternatives)

    if category is None:
        concept_pool = [
            item for item in matches(catalog) if item.category == "cn_concept"
        ]
        exact = [item for item in concept_pool if normalize_sector_name(item.name) == normalized]
        if exact:
            chosen = _prefer(exact)
            return SectorResolution(requested, (chosen,), alternatives=tuple(
                item for item in exact if item != chosen
            ))
        contains = [
            item for item in concept_pool
            if normalized in normalize_sector_name(item.name)
            or normalize_sector_name(item.name) in normalized
        ]
        if contains:
            chosen = _prefer(contains)
            return SectorResolution(requested, (chosen,), alternatives=tuple(
                item for item in contains if item != chosen
            ))

    raise SectorNotFound(f"未找到与“{requested}”匹配的行业或概念板块")


def _members_by_name(pool: list[SectorMatch], names: tuple[str, ...]) -> list[SectorMatch]:
    by_name: dict[str, SectorMatch] = {}
    for item in pool:
        key = normalize_sector_name(item.name)
        current = by_name.get(key)
        if current is None or (current.category != "industry" and item.category == "industry"):
            by_name[key] = item
    return [
        by_name[key] for key in (normalize_sector_name(name) for name in names)
        if key in by_name
    ]


def _prefer(items: list[SectorMatch]) -> SectorMatch:
    """Prefer the industry taxonomy, then the shortest, then the stable name."""

    return min(items, key=lambda item: (
        item.category != "industry",
        len(item.name),
        item.name,
    ))
