"""Storage transaction, authorization and HTTP contracts for explicit memory."""

from contextlib import closing
from datetime import date
from hashlib import sha256
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agents.run_lifecycle import RunControl, RunLifecycle
from app.database import connect
from app.memory_models import MemoryWrite
from app.models import AgentChatRequest, AgentChatResponse, AgentToolOutcome, AgentToolTrace
from app.repositories import ChatSessionRepository
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.routers.memories import router
from app.security import AnonymousVisitorMiddleware, SESSION_COOKIE_NAME, sign_visitor_owner_id
from app.services.runtime_coordination import LeaseLost
from database_support import reject_writes

OWNER = "visitor_" + "a" * 32
OTHER = "visitor_" + "b" * 32


def preference(**changes):
    fields = dict(memory_type="preference", key="response_style", value="concise",
                  source_user_message="请记住以后回答简洁")
    fields.update(changes)
    return MemoryWrite(**fields)


def test_receipt_failure_rolls_back_memory_and_owner_revision(postgresql_database):
    repository = LongTermMemoryRepository()
    with closing(connect()) as db:
        reject_writes(db, table="memory_operation_receipts", trigger="reject_memory_receipt")
        db.commit()
    with pytest.raises(Exception):
        repository.put(OWNER, preference(), "put-1")
    assert repository.list_active(OWNER) == []
    assert repository.get_owner_revision(OWNER) == 0
    with closing(connect()) as db:
        db.execute("DROP TRIGGER reject_memory_receipt ON memory_operation_receipts")
        db.commit()
    result = repository.put(OWNER, preference(), "put-1")
    assert result.current.revision == 1 and result.owner_revision == 1


def test_fenced_memory_mutation_requires_live_owner_and_exact_user_source(postgresql_database):
    ChatSessionRepository().ensure_session("memory-source", owner_id=OWNER)
    lifecycle = RunLifecycle()
    row, _ = lifecycle.create(AgentChatRequest(
        session_id="memory-source", message_id="m", message="请记住以后回答简洁",
    ), OWNER, runtime_version="durable-v1")
    lease = lifecycle.claim(row["run_id"])
    control = RunControl(lifecycle, row, lease)
    repository = LongTermMemoryRepository()
    with pytest.raises(ValueError, match="current user's"):
        repository.put(OWNER, preference(source_user_message="伪造的授权"), "bad-source", control=control)
    saved = repository.put(OWNER, preference(), "source-put", control=control)
    lease.release()
    replacement = lifecycle.claim(row["run_id"])
    try:
        with pytest.raises(LeaseLost):
            repository.put(OWNER, preference(), "source-put", control=control)
        with pytest.raises(LeaseLost):
            repository.delete(OWNER, saved.current.memory_id, 1, "source-delete", control=control)
        assert repository.get(OWNER, saved.current.memory_id).value == "concise"
    finally:
        replacement.release()


def test_mutation_receipt_returns_current_state_and_identity_from_one_transaction(postgresql_database):
    repository = LongTermMemoryRepository()
    first = repository.put(OWNER, preference(), "put")
    snapshot = repository.read_context(OWNER)
    assert first.context_fingerprint == snapshot["context_fingerprint"]
    assert first.owner_revision == snapshot["owner_revision"]
    deleted = repository.delete(OWNER, first.current.memory_id, 1, "delete")
    replayed = repository.put(OWNER, preference(), "put")
    assert replayed.current.deleted_at and replayed.current.value is None
    assert replayed.context_fingerprint == deleted.context_fingerprint
    assert replayed.owner_revision == deleted.owner_revision
    assert replayed.context_fingerprint != first.context_fingerprint


def _committed_archive_source(*, answer="本次固定样本共有两条研究记录。", extra_payload=None):
    session = "archive-" + uuid4().hex
    ChatSessionRepository().ensure_session(session, owner_id=OWNER)
    lifecycle = RunLifecycle()
    row, _ = lifecycle.create(AgentChatRequest(
        session_id=session, message_id="report", message="查询固定样本", trade_date=date(2026, 10, 23),
    ), OWNER)
    payload = {"trade_date": "2026-10-23", "source": "fixture://verified-market",
               "rows": [{"symbol": "000001", "name": "样本甲"}, {"symbol": "000002", "name": "样本乙"}],
               **(extra_payload or {})}
    lifecycle.finish(row["run_id"], OWNER, AgentChatResponse(
        session_id=session, intent="react_research", answer=answer, task_status="complete",
        generated_by="fixture", tool_results=[AgentToolTrace(name="fixture_market", summary="固定样本",
            status="success", output=payload, result=AgentToolOutcome(status="ok", payload=payload))],
    ).model_dump(mode="json"))
    return session, row["run_id"], payload


def test_archive_evidence_remains_verifiable_after_source_conversation_is_deleted(postgresql_database):
    session, run_id, payload = _committed_archive_source()
    repository = LongTermMemoryRepository()
    saved = repository.put(OWNER, MemoryWrite(memory_type="research_archive", key="固定样本报告",
        source_run_id=run_id, source_user_message="保存刚才的研究报告"), "archive-put")
    assert ChatSessionRepository().delete_session(session, owner_id=OWNER)
    restored = repository.get(OWNER, saved.current.memory_id)
    evidence = restored.metadata["evidence_snapshot"]
    assert evidence[0]["payload"] == payload
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    assert sha256(canonical.encode("utf-8")).hexdigest() == restored.metadata["evidence_sha256"]
    assert restored.value == saved.current.value
    assert restored.metadata["as_of"] == "2026-10-23"


@pytest.mark.parametrize("oversized", ["answer", "evidence"])
def test_archive_storage_limits_reject_without_truncation(postgresql_database, oversized):
    session, run_id, _ = _committed_archive_source(
        answer="x" * (64 * 1024 + 1) if oversized == "answer" else "固定研究报告。",
        extra_payload={"large_data": "x" * (1024 * 1024)} if oversized == "evidence" else None,
    )
    repository = LongTermMemoryRepository()
    with pytest.raises(ValueError, match="storage limit"):
        repository.put(OWNER, MemoryWrite(memory_type="research_archive", key="too-large",
            source_run_id=run_id, source_user_message="保存研究报告"), "oversized-put")
    assert repository.list_active(OWNER) == []


@pytest.fixture
def memory_api(postgresql_database):
    app = FastAPI()
    app.add_middleware(AnonymousVisitorMiddleware)
    app.include_router(router, prefix="/api/agents")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OWNER))
        yield client


def test_http_owner_crud_cas_policy_and_tombstone_receipt(memory_api):
    client = memory_api
    path = "/api/agents/chat/memories"
    body = {"value": "concise", "source_user_message": "请记住以后回答简洁"}
    created = client.put(path + "/preference/response_style", json=body, headers={"Idempotency-Key": "put-1"})
    assert created.status_code == 200, created.text
    memory_id = created.json()["current"]["memory_id"]
    listed = client.get(path)
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    assert listed.headers["cache-control"] == "private, no-store"
    no_key = client.put(path + "/preference/response_style", json=body)
    assert no_key.status_code == 422
    conflict = client.put(path + "/preference/response_style", json={**body, "value": "detailed"},
                          headers={"Idempotency-Key": "put-2"})
    assert conflict.status_code == 409
    forbidden = client.put(path + "/preference/response_style", json={**body, "value": "买入某股票"},
                           headers={"Idempotency-Key": "forbidden"})
    assert forbidden.status_code == 422
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OTHER))
    assert client.get(path + "/" + memory_id).status_code == 404
    assert client.get(path).json()["items"] == []
    assert client.delete(path + "/" + memory_id, params={"expected_revision": 1},
                         headers={"Idempotency-Key": "other-delete"}).status_code == 404
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OWNER))
    deleted = client.delete(path + "/" + memory_id, params={"expected_revision": 1},
                            headers={"Idempotency-Key": "delete-1"})
    assert deleted.status_code == 200 and deleted.json()["current"]["value"] is None
    assert client.get(path).json()["items"] == []
    replayed = client.put(path + "/preference/response_style", json=body,
                         headers={"Idempotency-Key": "put-1"})
    assert replayed.status_code == 200 and replayed.json()["replayed"]
    assert replayed.json()["current"]["deleted_at"]
    assert replayed.json()["current"]["source_user_message"] == ""
    assert client.get(path + "/" + memory_id).status_code == 404
