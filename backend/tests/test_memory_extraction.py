"""Background candidate extraction, confirmation and rejection."""

from contextlib import closing
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agents.run_lifecycle import RunLifecycle
from app.database import begin_write, connect
from app.memory_models import MemoryWrite
from app.models import AgentChatRequest, ChatSessionMessage
from app.repositories import ChatSessionRepository
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.routers.memories import router
from app.security import (
    AnonymousVisitorMiddleware,
    SESSION_COOKIE_NAME,
    sign_visitor_owner_id,
)
from app.services.llm_provider import LLMResult
from app.services.memory_extraction import (
    EXTRACTOR_VERSION,
    MemoryExtractionWorker,
    enqueue_memory_extraction_in_connection,
)


MESSAGE = "以后展示龙虎榜只显示股票名称"


class CandidateProvider:
    def __init__(self, memories):
        self.memories = memories

    def generate_function_call(self, *args, **kwargs):
        return LLMResult(
            content=json.dumps({"memories": self.memories}, ensure_ascii=False),
            model="fixture", provider="fixture",
        )


def draft(**changes):
    value = {
        "memory_type": "preference",
        "content_text": "展示龙虎榜时只显示股票名称",
        "canonical_key": "tool_display:dragon_tiger_list",
        "scope_type": "tool",
        "scope": {"tool_name": "dragon_tiger_list"},
        "structured": {"display_columns": ["name"]},
        "confidence": 0.95,
        "stability": "explicit_future",
        "source_quote": MESSAGE,
        "reason": "用户明确表示以后展示龙虎榜只显示名称",
    }
    value.update(changes)
    return value


def seed_job(owner=None):
    user = owner or "visitor_" + uuid4().hex
    session = "memory-extract-" + uuid4().hex
    chats = ChatSessionRepository()
    chats.ensure_session(session, owner_id=user)
    lifecycle = RunLifecycle()
    row, _ = lifecycle.create(AgentChatRequest(
        session_id=session, message_id="m1", message=MESSAGE,
    ), user)
    chats.append_message(ChatSessionMessage(
        message_id="user_" + row["run_id"], session_id=session, role="user",
        content=MESSAGE, run_id=row["run_id"], created_at=row["started_at"],
    ), owner_id=user)
    with closing(connect()) as db:
        begin_write(db)
        job_id = enqueue_memory_extraction_in_connection(
            db, run_id=row["run_id"], owner_id=user, session_id=session,
            source_message_id="user_" + row["run_id"], task_status="complete",
        )
        db.commit()
    return user, session, row, job_id


def test_worker_creates_candidate_without_activating_it(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, session, row, job_id = seed_job()
    assert job_id
    worker = MemoryExtractionWorker(provider=CandidateProvider([draft()]))
    assert worker.run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    candidates = repository.list_candidates(owner)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["status"] == "candidate"
    assert candidate["source_kind"] == "explicit" or candidate["source_kind"] == "auto"
    assert candidate["value"] == "展示龙虎榜时只显示股票名称"
    assert candidate["scope"] == {"tool_name": "dragon_tiger_list"}
    assert candidate["structured"] == {"display_columns": ["name"]}
    assert candidate["source_quote"] == MESSAGE
    assert repository.list_active(owner) == []

    confirmed = repository.confirm_candidate(
        owner, candidate["memory_id"], "confirm-candidate",
    )
    assert confirmed.current.status == "active"
    assert repository.list_active(owner)[0]["key"] == "tool_display:dragon_tiger_list"


def test_worker_skips_invalid_quote_and_trading_candidate(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, _, _, _ = seed_job()
    provider = CandidateProvider([
        draft(source_quote="不存在的原文"),
        draft(content_text="以后直接告诉我买入哪一只", source_quote=MESSAGE),
    ])
    assert MemoryExtractionWorker(provider=provider).run_once(limit=1) == 1
    assert LongTermMemoryRepository().list_candidates(owner) == []


def test_auto_learn_off_does_not_enqueue_or_process(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "off")
    owner, _, _, job_id = seed_job()
    assert job_id is None
    assert MemoryExtractionWorker(provider=CandidateProvider([draft()])).run_once() == 0
    assert LongTermMemoryRepository().list_candidates(owner) == []


def test_candidate_api_lists_confirms_and_rejects(postgresql_database):
    owner = "visitor_" + "c" * 32
    repository = LongTermMemoryRepository()
    write = MemoryWrite(
        memory_type="preference", key="tool_display:dragon_tiger_list",
        value="展示龙虎榜时只显示股票名称", scope_type="tool",
        scope={"tool_name": "dragon_tiger_list"},
        structured={"display_columns": ["name"]},
        source_quote=MESSAGE, source_user_message=MESSAGE,
    )
    first = repository.consolidate_candidate(owner, write, "candidate-1")
    second = repository.consolidate_candidate(
        owner, write.model_copy(update={"key": "preference:second", "value": "以后优先看换手率"}),
        "candidate-2",
    )
    first_record = repository.get(owner, first["candidate_id"], include_inactive=True)
    second_record = repository.get(owner, second["candidate_id"], include_inactive=True)

    app = FastAPI()
    app.add_middleware(AnonymousVisitorMiddleware)
    app.include_router(router, prefix="/api/agents")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(owner))
        listed = client.get("/api/agents/chat/memories/candidates")
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 2
        confirmed = client.post(
            f"/api/agents/chat/memories/candidates/{first_record.memory_id}/confirm",
            headers={"Idempotency-Key": "api-confirm"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["current"]["status"] == "active"
        rejected = client.post(
            f"/api/agents/chat/memories/candidates/{second_record.memory_id}/reject",
            params={"expected_revision": second_record.revision},
            headers={"Idempotency-Key": "api-reject"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["current"]["status"] == "revoked"
        assert client.get("/api/agents/chat/memories/candidates").json()["items"] == []


def test_worker_creates_an_update_candidate_against_the_exact_active_revision(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, _, _, job_id = seed_job()
    active = LongTermMemoryRepository().put(owner, MemoryWrite(
        memory_type="preference", key="tool_display:dragon_tiger_list",
        value="展示龙虎榜时显示代码和名称", scope_type="tool",
        scope={"tool_name": "dragon_tiger_list"},
        structured={"display_columns": ["symbol", "name"]},
        source_user_message=MESSAGE,
    ), "active-display")

    assert MemoryExtractionWorker(provider=CandidateProvider([draft()])).run_once(limit=1) == 1
    candidates = LongTermMemoryRepository().list_candidates(owner)
    assert len(candidates) == 1
    assert candidates[0]["supersedes_memory_id"] == active.current.memory_id
    assert candidates[0]["supersedes_revision"] == active.current.revision
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert result["decisions"]["update"] == 1


def test_worker_does_not_create_a_candidate_when_active_content_already_matches(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, _, _, job_id = seed_job()
    LongTermMemoryRepository().put(owner, MemoryWrite(
        memory_type="preference", key="tool_display:dragon_tiger_list",
        value="展示龙虎榜时只显示名称", scope_type="tool",
        scope={"tool_name": "dragon_tiger_list"},
        structured={"display_columns": ["name"]},
        source_user_message=MESSAGE,
    ), "active-display")

    assert MemoryExtractionWorker(provider=CandidateProvider([draft()])).run_once(limit=1) == 1
    assert LongTermMemoryRepository().list_candidates(owner) == []
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert result["decisions"]["noop"] == 1


def test_worker_normalizes_tool_id_and_skips_low_confidence_candidates(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, _, _, _ = seed_job()
    provider = CandidateProvider([
        draft(scope={"tool_id": "dragon_tiger_list"}),
        draft(
            canonical_key="preference:low-confidence",
            content_text="以后回答尽量详细",
            scope_type="global", scope={}, structured={},
            confidence=0.5, stability="conditional_scope",
            reason="临时偏好",
        ),
    ])
    assert MemoryExtractionWorker(provider=provider).run_once(limit=1) == 1
    candidates = LongTermMemoryRepository().list_candidates(owner)
    assert len(candidates) == 1
    assert candidates[0]["scope"] == {"tool_name": "dragon_tiger_list"}
    assert candidates[0]["key"] == "tool_display:dragon_tiger_list"
