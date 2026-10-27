"""Candidate consolidation, exact-target confirmation and scope identity."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.database import begin_write, connect
from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository, MemoryNotFound
from app.services.memory_retrieval import retrieve_memories


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


def test_delete_candidate_targets_the_exact_active_revision_and_removes_it_on_confirm(
    postgresql_database,
):
    repository = LongTermMemoryRepository()
    user = owner()
    active = repository.put(user, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "active-style")
    deletion = MemoryWrite(
        memory_type="preference", key="response_style",
        value="不要再记住回答要简洁",
        source_user_message="以后不要再记住回答要简洁",
    )
    decision = repository.consolidate_candidate(
        user, deletion, "candidate-delete", action="delete",
    )

    assert decision["decision"] == "delete"
    candidate = repository.get(user, decision["candidate_id"], include_inactive=True)
    assert candidate.candidate_action == "delete"
    assert candidate.supersedes_memory_id == active.current.memory_id
    assert candidate.supersedes_revision == active.current.revision
    assert repository.get(user, active.current.memory_id).status == "active"

    confirmed = repository.confirm_candidate(user, candidate.memory_id, "confirm-delete")
    assert confirmed.current.status == "deleted"
    assert confirmed.owner_revision == 2
    old = repository.get(user, active.current.memory_id, include_inactive=True)
    assert old.status == "deleted" and old.value is None
    assert repository.list_active(user) == []
    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (user, candidate.memory_id, active.current.memory_id)).fetchone()
    assert relation["relation"] == "supersedes"


def test_delete_without_an_active_target_creates_no_candidate(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    decision = repository.consolidate_candidate(
        user,
        MemoryWrite(
            memory_type="preference", key="response_style",
            value="不要再记住回答要简洁",
            source_user_message="以后不要再记住回答要简洁",
        ),
        "candidate-delete-missing",
        action="delete",
    )
    assert decision["decision"] == "noop"
    assert repository.list_candidates(user) == []


def test_new_delete_proposal_supersedes_a_pending_update_candidate(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    repository.put(user, display_memory(), "active-display")
    pending_update = repository.consolidate_candidate(
        user, display_memory(columns=("symbol", "name")), "candidate-update",
    )
    deletion = repository.consolidate_candidate(
        user,
        MemoryWrite(
            memory_type="preference", key="tool_display:dragon_tiger_list",
            value="不要再记住龙虎榜展示偏好",
            scope_type="tool", scope={"tool_name": "dragon_tiger_list"},
            source_user_message="以后不要再记住龙虎榜展示偏好",
        ),
        "candidate-delete",
        action="delete",
    )
    assert deletion["decision"] == "delete"
    old = repository.get(user, pending_update["candidate_id"], include_inactive=True)
    assert old.status == "superseded"
    assert [item["memory_id"] for item in repository.list_candidates(user)] == [
        deletion["candidate_id"],
    ]


def test_scoped_setting_is_an_exception_to_the_global_setting(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    global_setting = repository.put(user, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "global-style")
    scoped = repository.put(user, MemoryWrite(
        memory_type="preference", key="response_style", value="detailed",
        scope_type="tool", scope={"tool_name": "stock_financials"},
        source_user_message="看财报时详细一点",
    ), "scoped-style")

    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (user, scoped.current.memory_id, global_setting.current.memory_id)).fetchone()
    assert relation["relation"] == "exception_to"

    matching = retrieve_memories(
        repository, owner_id=user, query="研究财报", scope_hint={"tool_name": "stock_financials"},
    )
    assert [item["value"] for item in matching.items] == ["detailed"]
    assert matching.diagnostics["memory_exception_suppressed_count"] == 1

    other = retrieve_memories(
        repository, owner_id=user, query="研究财报", scope_hint={"tool_name": "stock_kline"},
    )
    assert [item["value"] for item in other.items] == ["concise"]


def test_global_setting_added_later_links_existing_scoped_exception(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    scoped = repository.put(user, MemoryWrite(
        memory_type="preference", key="response_style", value="detailed",
        scope_type="tool", scope={"tool_name": "stock_financials"},
        source_user_message="看财报时详细一点",
    ), "scoped-style")
    global_setting = repository.put(user, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "global-style")

    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (user, scoped.current.memory_id, global_setting.current.memory_id)).fetchone()
    assert relation["relation"] == "exception_to"


def test_candidate_ttl_is_set_hidden_and_cleared_on_expiry(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    decision = repository.consolidate_candidate(
        user, display_memory(), "ttl-candidate", candidate_ttl_days=30,
    )
    candidate = repository.get(user, decision["candidate_id"], include_inactive=True)
    assert candidate.candidate_expires_at is not None
    with closing(connect()) as db:
        begin_write(db)
        db.execute("""UPDATE long_term_memories SET candidate_expires_at=clock_timestamp()-interval '1 second'
            WHERE memory_id=?""", (candidate.memory_id,))
        db.commit()
    assert repository.list_candidates(user) == []
    assert repository.expire_candidates() >= 1
    expired = repository.get(user, candidate.memory_id, include_inactive=True)
    assert expired.status == "expired"
    assert expired.value is None
    assert expired.candidate_expires_at is None
    assert repository.expire_candidates() == 0


def test_confirming_a_candidate_clears_its_review_expiry(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    decision = repository.consolidate_candidate(user, display_memory(), "ttl-confirm")
    confirmed = repository.confirm_candidate(user, decision["candidate_id"], "ttl-confirm-op")
    assert confirmed.current.status == "active"
    assert confirmed.current.candidate_expires_at is None


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

def test_expired_active_memories_are_swept_without_content_revision_change(postgresql_database):
    repository = LongTermMemoryRepository()
    user = owner()
    memory = display_memory().model_copy(update={
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
    })
    saved = repository.put(user, memory, "expired-active")
    assert repository.list_active(user) == []
    assert repository.get(user, saved.current.memory_id, include_inactive=True).status == "active"

    assert repository.expire_active_memories() == 1
    current = repository.get(user, saved.current.memory_id, include_inactive=True)
    assert current.status == "expired"
    assert current.revision == saved.current.revision
    assert repository.get_owner_revision(user) == saved.owner_revision
    assert repository.expire_active_memories() == 0
