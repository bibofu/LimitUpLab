"""Candidate consolidation, exact-target confirmation and scope identity."""

from contextlib import closing
from uuid import uuid4

import pytest

from app.database import connect
from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository, MemoryNotFound


def owner():
    return "visitor_" + uuid4().hex


def display_memory(*, columns=("name",), value="展示龙虎榜时只显示股票名称"):
    return MemoryWrite(
        memory_type="preference",
        key="tool_display:dragon_tiger_list",
        value=value,
        scope_type="tool",
        scope={"tool_name": "dragon_tiger_list"},
        structured={"display_columns": list(columns)},
        source_quote="以后展示龙虎榜只显示名称",
        source_user_message="以后展示龙虎榜只显示名称",
    )


def topic_memory(topic, value):
    return MemoryWrite(
        memory_type="preference",
        key="preference:risk",
        value=value,
        scope_type="topic",
        scope={"topic": topic},
        source_user_message=value,
    )


def test_repeated_active_content_is_a_noop_without_a_duplicate_candidate(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    repository.put(user, display_memory(), "active-display")

    decision = repository.consolidate_candidate(
        user,
        display_memory(value="龙虎榜只显示股票名称"),
        "candidate-repeat",
    )

    assert decision["decision"] == "noop"
    assert decision["candidate_id"] is None
    assert decision["target_memory_id"]
    assert repository.list_candidates(user) == []
    assert len(repository.list_active(user)) == 1


def test_changed_active_content_creates_an_update_candidate_and_confirms_atomically(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    active = repository.put(user, display_memory(), "active-display")
    decision = repository.consolidate_candidate(
        user, display_memory(columns=("symbol", "name")), "candidate-update",
    )

    assert decision["decision"] == "update"
    assert decision["target_memory_id"] == active.current.memory_id
    candidate = repository.get(user, decision["candidate_id"], include_inactive=True)
    assert candidate.status == "candidate"
    assert candidate.supersedes_memory_id == active.current.memory_id
    assert candidate.supersedes_revision == active.current.revision
    assert repository.get(user, active.current.memory_id).value == active.current.value

    confirmed = repository.confirm_candidate(user, candidate.memory_id, "confirm-update")
    assert confirmed.current.status == "active"
    assert confirmed.owner_revision == 2
    assert confirmed.current.structured == {"display_columns": ["symbol", "name"]}
    old = repository.get(user, active.current.memory_id, include_inactive=True)
    assert old.status == "superseded"
    assert [item["memory_id"] for item in repository.list_active(user)] == [candidate.memory_id]
    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (user, candidate.memory_id, active.current.memory_id)).fetchone()
    assert relation["relation"] == "supersedes"

    replay = repository.confirm_candidate(user, candidate.memory_id, "confirm-update")
    assert replay.replayed is True
    assert len(repository.list_active(user)) == 1


def test_newer_proposal_supersedes_the_pending_candidate_and_reuses_one_identity(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    first = repository.consolidate_candidate(user, display_memory(), "candidate-first")
    second = repository.consolidate_candidate(
        user, display_memory(columns=("symbol", "name")), "candidate-second",
    )

    assert first["decision"] == "add"
    assert second["decision"] == "add"
    assert second["superseded_candidate_id"] == first["candidate_id"]
    assert [item["memory_id"] for item in repository.list_candidates(user)] == [
        second["candidate_id"],
    ]
    old = repository.get(user, first["candidate_id"], include_inactive=True)
    assert old.status == "superseded"
    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (user, second["candidate_id"], first["candidate_id"])).fetchone()
    assert relation["relation"] == "supersedes"

    repeated = repository.consolidate_candidate(
        user, display_memory(columns=("symbol", "name")), "candidate-second-repeat",
    )
    assert repeated["decision"] == "noop"
    assert repeated["candidate_id"] == second["candidate_id"]
    assert len(repository.list_candidates(user)) == 1


def test_explicit_write_supersedes_a_pending_candidate_for_the_same_identity(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    active = repository.put(user, display_memory(), "active-display")
    pending = repository.consolidate_candidate(
        user, display_memory(columns=("symbol", "name")), "candidate-update",
    )

    override = display_memory(columns=("name",), value="展示龙虎榜时只显示名称和代码")
    override = override.model_copy(update={"expected_revision": active.current.revision})
    repository.put(
        user,
        override,
        "explicit-override",
        expected_owner_revision=1,
    )

    stale = repository.get(user, pending["candidate_id"], include_inactive=True)
    assert stale.status == "superseded"
    with pytest.raises(MemoryNotFound):
        repository.confirm_candidate(user, pending["candidate_id"], "confirm-stale")
    assert repository.get(user, active.current.memory_id).status == "active"


def test_same_key_can_coexist_across_scopes_instead_of_overwriting(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    repository.put(user, topic_memory("首板", "研究首板优先看换手率"), "first-board")
    repository.put(user, topic_memory("炸板", "研究炸板优先看回封时间"), "failed-board")

    active = repository.list_active(user)
    assert len(active) == 2
    assert {item["scope"]["topic"] for item in active} == {"首板", "炸板"}

    decision = repository.consolidate_candidate(
        user, topic_memory("首板", "研究首板优先看换手率和回封时间"), "candidate-topic-update",
    )
    assert decision["decision"] == "update"
    assert decision["target_memory_id"] == next(
        item["memory_id"] for item in active if item["scope"]["topic"] == "首板"
    )
