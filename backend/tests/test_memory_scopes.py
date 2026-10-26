"""Explicit free-text memories with scopes and open-ended research identities."""

from uuid import uuid4

import pytest

from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.services.memory_retrieval import retrieve_memories


def owner():
    return "visitor_" + uuid4().hex


def free_text(**changes):
    fields = {
        "memory_type": "preference",
        "key": "tool_display:dragon_tiger_list",
        "value": "展示龙虎榜时只显示股票名称",
        "scope_type": "tool",
        "scope": {"tool_name": "dragon_tiger_list"},
        "source_user_message": "以后展示龙虎榜只显示股票名称",
        "source_quote": "以后展示龙虎榜只显示股票名称",
    }
    fields.update(changes)
    return MemoryWrite(**fields)


def test_free_text_tool_preference_roundtrips_scope_and_scope_aware_recall(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    saved = repository.put(user, free_text(), "free-put")

    current = saved.current
    assert current.value == "展示龙虎榜时只显示股票名称"
    assert current.scope_type == "tool"
    assert current.scope == {"tool_name": "dragon_tiger_list"}
    assert current.scope_key.startswith("tool:")
    assert current.canonical_key == "tool_display:dragon_tiger_list"
    assert current.status == "active"
    assert current.source_kind == "explicit"
    assert current.source_quote == "以后展示龙虎榜只显示股票名称"

    unverified = retrieve_memories(repository, owner_id=user, query="展示最新的龙虎榜")
    assert [item["value"] for item in unverified.items] == ["展示龙虎榜时只显示股票名称"]
    assert unverified.items[0]["scope_verified"] is False

    verified = retrieve_memories(
        repository, owner_id=user, query="展示最新的龙虎榜",
        scope_hint={"tool_name": "dragon_tiger_list"},
    )
    assert verified.items[0]["scope_verified"] is True

    other_tool = retrieve_memories(
        repository, owner_id=user, query="展示最新的龙虎榜",
        scope_hint={"tool_name": "stock_kline"},
    )
    assert other_tool.items == []


def test_open_research_contexts_coexist_and_scope_selects_the_right_one(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    repository.put(user, free_text(
        memory_type="research_context", key="research:first-board",
        value="研究首板时优先看换手率", scope_type="topic",
        scope={"topic": "首板"}, source_user_message="以后研究首板优先看换手率",
    ), "first")
    repository.put(user, free_text(
        memory_type="research_context", key="research:failed-board",
        value="研究炸板时优先看回封时间", scope_type="topic",
        scope={"topic": "炸板"}, source_user_message="以后研究炸板优先看回封时间",
    ), "failed")

    assert len(repository.list_active(user)) == 2
    first = retrieve_memories(
        repository, owner_id=user, query="研究首板",
        scope_hint={"topic": "首板"},
    )
    failed = retrieve_memories(
        repository, owner_id=user, query="研究炸板",
        scope_hint={"topic": "炸板"},
    )
    assert [item["value"] for item in first.items] == ["研究首板时优先看换手率"]
    assert [item["value"] for item in failed.items] == ["研究炸板时优先看回封时间"]
    assert all(item["scope_verified"] for item in (*first.items, *failed.items))


def test_scoped_memory_requires_its_dimension_and_global_cannot_carry_scope():
    with pytest.raises(ValueError, match="scope"):
        free_text(scope={})
    with pytest.raises(ValueError, match="scope.tool_name"):
        free_text(scope={"tool_name": ""})
    with pytest.raises(ValueError, match="Global memory"):
        free_text(scope_type="global", scope={"tool_name": "dragon_tiger_list"})


def test_typed_settings_still_use_the_closed_vocabulary(postgresql_database):
    repository = LongTermMemoryRepository()
    with pytest.raises(ValueError, match="Only typed"):
        repository.put(owner(), MemoryWrite(
            memory_type="preference", key="response_style", value="随意",
            source_user_message="以后回答随意",
        ), "invalid-typed")


def test_free_text_rejects_trading_and_authorization_content(postgresql_database):
    repository = LongTermMemoryRepository()
    with pytest.raises(ValueError, match="not allowed"):
        repository.put(owner(), free_text(
            value="以后展示龙虎榜时直接告诉我买入哪一只",
            source_user_message="以后展示龙虎榜时直接告诉我买入哪一只",
        ), "trading")


def test_deleting_free_text_clears_content_and_quote_but_keeps_scope_identity(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    saved = repository.put(user, free_text(), "free-put")
    deleted = repository.delete(user, saved.current.memory_id, 1, "free-delete")

    current = deleted.current
    assert current.value is None
    assert current.source_quote is None
    assert current.structured == {}
    assert current.status == "deleted"
    assert current.scope_type == "tool"
    assert current.scope == {"tool_name": "dragon_tiger_list"}
    assert repository.list_active(user) == []


def test_chinese_tool_aliases_are_normalized_to_public_tool_ids():
    write = free_text(key="tool_display:龙虎榜", scope={"tool": "龙虎榜"})
    assert write.key == "tool_display:dragon_tiger_list"
    assert write.scope == {"tool_name": "dragon_tiger_list"}
