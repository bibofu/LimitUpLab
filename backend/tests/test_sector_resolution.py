"""Deterministic sector-name resolution over the persisted catalog."""

import pytest

from app.services.sector_resolution import (
    SectorNotFound, normalize_sector_name, resolve_sector,
)


CATALOG = [
    {"thscode": "881125.TI", "name": "汽车整车", "category": "industry", "in_summary": True},
    {"thscode": "881126.TI", "name": "汽车零部件", "category": "industry", "in_summary": True},
    {"thscode": "881128.TI", "name": "汽车服务及其他", "category": "industry", "in_summary": True},
    {"thscode": "884292.TI", "name": "其他汽车零部件", "category": "industry", "in_summary": False},
    {"thscode": "881121.TI", "name": "半导体", "category": "industry", "in_summary": True},
    {"thscode": "885728.TI", "name": "人工智能", "category": "cn_concept", "in_summary": False},
    {"thscode": "885431.TI", "name": "新能源汽车", "category": "cn_concept", "in_summary": False},
    {"thscode": "881141.TI", "name": "军工装备", "category": "industry", "in_summary": True},
    {"thscode": "881142.TI", "name": "军工电子", "category": "industry", "in_summary": True},
]


def test_exact_name_beats_contains_matches():
    resolved = resolve_sector(CATALOG, "半导体")
    assert [item.name for item in resolved.members] == ["半导体"]
    assert resolved.members[0].in_summary is True
    assert not resolved.group


def test_broad_car_name_is_a_group_of_same_level_industries():
    resolved = resolve_sector(CATALOG, "汽车板块最近走势")
    assert resolved.group is True
    assert [item.name for item in resolved.members] == [
        "汽车整车", "汽车零部件", "汽车服务及其他",
    ]
    assert resolved.category == "industry"


def test_explicit_sub_sector_is_not_expanded_to_the_group():
    resolved = resolve_sector(CATALOG, "汽车整车")
    assert [item.name for item in resolved.members] == ["汽车整车"]
    assert not resolved.group


def test_concept_alias_and_category_filter():
    resolved = resolve_sector(CATALOG, "AI")
    assert [item.name for item in resolved.members] == ["人工智能"]
    assert resolved.category == "cn_concept"

    with pytest.raises(SectorNotFound):
        resolve_sector(CATALOG, "人工智能", category="industry")


def test_contains_match_prefers_industry_and_exposes_alternatives():
    resolved = resolve_sector(CATALOG, "新能源")
    assert [item.name for item in resolved.members] == ["新能源汽车"]
    assert resolved.category == "cn_concept"


def test_unknown_name_is_explicit():
    with pytest.raises(SectorNotFound):
        resolve_sector(CATALOG, "不存在的板块")


def test_normalization_removes_board_suffixes():
    assert normalize_sector_name(" 今天 A股 汽车板块 ") == "汽车"
