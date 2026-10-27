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
    MemoryCandidateDraft,
    MemoryExtractionWorker,
    candidate_ttl_days,
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


class JudgeCandidateProvider(CandidateProvider):
    def __init__(self, memories, verdict):
        super().__init__(memories)
        self.verdict = verdict
        self.judge_calls = 0

    def generate_function_call(self, *args, **kwargs):
        if kwargs.get("function_name") == "submit_memory_judgement":
            self.judge_calls += 1
            return LLMResult(
                content=json.dumps(self.verdict, ensure_ascii=False),
                model="fixture", provider="fixture",
            )
        return super().generate_function_call(*args, **kwargs)


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


def test_candidate_ttl_environment_is_bounded(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS", "7")
    assert candidate_ttl_days() == 7
    monkeypatch.setenv("LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS", "0")
    assert candidate_ttl_days() == 30
    monkeypatch.setenv("LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS", "abc")
    assert candidate_ttl_days() == 30


def test_tool_display_canonical_key_uses_the_public_tool_identity():
    item = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "dragon_tiger_list.display_columns",
    })
    assert item.canonical_key == "tool_display:dragon_tiger_list"


def test_model_typed_hints_are_normalized_to_closed_settings():
    style = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": None,
        "content_text": "用户偏好简洁的回答",
        "scope_type": "global",
        "scope": {},
        "structured": {"style": "concise"},
        "source_quote": "以后回答简洁一点",
    })
    assert (style.memory_type, style.canonical_key, style.content_text) == (
        "preference", "response_style", "concise",
    )
    assert style.structured == {}

    output = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "display.default_format",
        "content_text": "用户希望默认用表格",
        "scope_type": "global",
        "scope": {},
        "structured": {"display_format": "table"},
        "source_quote": "以后默认用表格",
    })
    assert (output.memory_type, output.canonical_key, output.content_text) == (
        "preference", "output_format", "table",
    )

    topic = MemoryCandidateDraft.model_validate({
        **draft(),
        "memory_type": "preference",
        "canonical_key": "topic_filter_first_limit_up",
        "content_text": "只看首板",
        "scope_type": "topic",
        "scope": {"topic": "首板"},
        "structured": {"filter": "首板"},
        "source_quote": "以后研究主题只看首板",
    })
    assert (topic.memory_type, topic.canonical_key, topic.content_text) == (
        "research_context", "research_topic", "首板",
    )
    assert (topic.scope_type, topic.scope, topic.structured) == ("global", {}, {})

    topic_alias = MemoryCandidateDraft.model_validate({
        **draft(),
        "memory_type": "research_context",
        "canonical_key": "research_topic",
        "content_text": "首板",
        "scope_type": "topic",
        "scope": {"research_topic": "首板"},
        "structured": {},
        "source_quote": "以后研究主题只看首板",
    })
    assert (topic_alias.canonical_key, topic_alias.scope_type, topic_alias.scope) == (
        "research_topic", "global", {},
    )

    dotted = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "preference.response_style",
        "content_text": "concise",
        "scope_type": "global",
        "scope": {},
        "structured": {},
        "source_quote": "以后回答简洁一点",
    })
    assert (dotted.memory_type, dotted.canonical_key, dotted.content_text) == (
        "preference", "response_style", "concise",
    )

    scoped_topic = MemoryCandidateDraft.model_validate({
        **draft(),
        "memory_type": "research_context",
        "canonical_key": None,
        "content_text": "只看首板",
        "scope_type": "tool",
        "scope": {"tool_name": "stock_financials"},
        "structured": {"filter": "首板"},
        "source_quote": "以后分析财报时研究主题只看首板",
    })
    assert (scoped_topic.canonical_key, scoped_topic.scope_type) == (
        "research_topic", "tool",
    )

    conditional = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "preference.response_style",
        "content_text": "detailed",
        "scope_type": "condition",
        "scope": {"condition": "分析财报时"},
        "structured": {},
        "source_quote": "以后分析财报时详细一点",
    })
    assert (conditional.canonical_key, conditional.scope_type, conditional.scope) == (
        "response_style", "tool", {"tool_name": "stock_financials"},
    )

    entity_condition = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "preference.response_style",
        "content_text": "detailed",
        "scope_type": "condition",
        "scope": {"condition": "分析600519时"},
        "structured": {},
        "source_quote": "以后分析600519时详细一点",
    })
    assert (entity_condition.canonical_key, entity_condition.scope_type,
            entity_condition.scope) == (
        "response_style", "entity", {"symbol": "600519"},
    )

    dotted_condition = MemoryCandidateDraft.model_validate({
        **draft(),
        "canonical_key": "preference.response_style.market_crash",
        "content_text": "市场大跌时希望分析更详细",
        "scope_type": "condition",
        "scope": {"condition": "市场大跌"},
        "structured": {"preference.response_style": "detailed"},
        "source_quote": "以后市场大跌时详细一点",
        "confidence": 0.75,
        "stability": "conditional_scope",
    })
    assert (dotted_condition.canonical_key, dotted_condition.content_text,
            dotted_condition.scope, dotted_condition.structured) == (
        "response_style", "detailed", {"condition": "市场大跌"}, {},
    )


def seed_job(owner=None, message=MESSAGE):
    user = owner or "visitor_" + uuid4().hex
    session = "memory-extract-" + uuid4().hex
    chats = ChatSessionRepository()
    chats.ensure_session(session, owner_id=user)
    lifecycle = RunLifecycle()
    row, _ = lifecycle.create(AgentChatRequest(
        session_id=session, message_id="m1", message=message,
    ), user)
    chats.append_message(ChatSessionMessage(
        message_id="user_" + row["run_id"], session_id=session, role="user",
        content=message, run_id=row["run_id"], created_at=row["started_at"],
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


def test_worker_creates_a_delete_candidate_against_an_existing_memory(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    owner, _, _, job_id = seed_job()
    active = LongTermMemoryRepository().put(owner, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "active-style")
    deletion = draft(
        candidate_action="delete",
        memory_type="preference",
        canonical_key="response_style",
        content_text="不要再记住回答要简洁",
        scope_type="global",
        scope={},
        structured={},
        source_quote=MESSAGE,
        stability="explicit_correction",
    )

    assert MemoryExtractionWorker(provider=CandidateProvider([deletion])).run_once(limit=1) == 1
    candidates = LongTermMemoryRepository().list_candidates(owner)
    assert len(candidates) == 1
    assert candidates[0]["candidate_action"] == "delete"
    assert candidates[0]["supersedes_memory_id"] == active.current.memory_id
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert result["decisions"]["delete"] == 1

    confirmed = LongTermMemoryRepository().confirm_candidate(
        owner, candidates[0]["memory_id"], "confirm-delete-candidate",
    )
    assert confirmed.current.status == "deleted"
    assert LongTermMemoryRepository().list_active(owner) == []


def test_worker_safe_auto_activates_only_the_low_risk_tool_display_add(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    owner, _, _, job_id = seed_job()
    assert MemoryExtractionWorker(provider=CandidateProvider([draft()])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    assert repository.list_candidates(owner) == []
    active = repository.list_active(owner)
    assert len(active) == 1
    assert active[0]["activation_kind"] == "safe_auto"
    assert active[0]["source_kind"] == "auto"
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert len(result["auto_activated_ids"]) == 1
    assert result["auto_mode"] == "safe_auto"


def test_worker_safe_auto_activates_a_closed_global_typed_add(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    message = "以后回答简洁一点"
    owner, _, _, _ = seed_job(message=message)
    typed = draft(
        canonical_key=None,
        content_text="用户偏好简洁的回答",
        scope_type="global",
        scope={},
        structured={"style": "concise"},
        source_quote=message,
    )
    assert MemoryExtractionWorker(provider=CandidateProvider([typed])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    active = repository.list_active(owner)
    assert len(active) == 1
    assert (active[0]["key"], active[0]["value"]) == ("response_style", "concise")
    assert active[0]["activation_kind"] == "safe_auto"
    assert repository.list_candidates(owner) == []


def test_worker_safe_auto_activates_a_tool_scoped_typed_exception(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    message = "以后分析财报时详细一点"
    owner, _, _, _ = seed_job(message=message)
    global_setting = LongTermMemoryRepository().put(owner, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "global-style-before-scoped-auto")
    scoped = draft(
        canonical_key="response_style",
        content_text="detailed",
        scope_type="tool",
        scope={"tool_name": "stock_financials"},
        structured={"preference": {"response_style": "detailed"}},
        source_quote=message,
        confidence=0.85,
        stability="conditional_scope",
    )
    assert MemoryExtractionWorker(provider=CandidateProvider([scoped])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    active = repository.list_active(owner)
    assert len(active) == 2
    scoped_active = next(item for item in active if item["scope"])
    assert (scoped_active["key"], scoped_active["value"]) == ("response_style", "detailed")
    assert scoped_active["scope"] == {"tool_name": "stock_financials"}
    assert scoped_active["activation_kind"] == "safe_auto"
    assert repository.list_candidates(owner) == []
    with closing(connect()) as db:
        relation = db.execute("""SELECT relation FROM memory_relations
            WHERE owner_id=? AND from_memory_id=? AND to_memory_id=?""",
            (owner, scoped_active["memory_id"], global_setting.current.memory_id)).fetchone()
    assert relation["relation"] == "exception_to"


def test_worker_safe_auto_activates_an_entity_scoped_typed_exception(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    message = "以后分析600519时详细一点"
    owner, _, _, _ = seed_job(message=message)
    scoped = draft(
        canonical_key="preference.response_style",
        content_text="detailed",
        scope_type="entity",
        scope={"symbol": "600519"},
        structured={},
        source_quote=message,
        confidence=0.9,
        stability="explicit_future",
    )
    assert MemoryExtractionWorker(provider=CandidateProvider([scoped])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    active = repository.list_active(owner)
    assert len(active) == 1
    assert (active[0]["key"], active[0]["value"], active[0]["scope"]) == (
        "response_style", "detailed", {"symbol": "600519"},
    )
    assert active[0]["activation_kind"] == "safe_auto"
    assert repository.list_candidates(owner) == []


def test_worker_safe_auto_activates_a_condition_scoped_typed_exception(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    message = "以后市场大跌时详细一点"
    owner, _, _, _ = seed_job(message=message)
    scoped = draft(
        canonical_key="preference.response_style",
        content_text="detailed",
        scope_type="condition",
        scope={"condition": "市场大跌时"},
        structured={},
        source_quote=message,
        confidence=0.85,
        stability="conditional_scope",
    )
    assert MemoryExtractionWorker(provider=CandidateProvider([scoped])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    active = repository.list_active(owner)
    assert len(active) == 1
    assert (active[0]["key"], active[0]["value"], active[0]["scope"]) == (
        "response_style", "detailed", {"condition": "市场大跌时"},
    )
    assert active[0]["activation_kind"] == "safe_auto"
    assert repository.list_candidates(owner) == []


def test_worker_judge_activates_a_borderline_condition_candidate(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto_judge")
    message = "以后市场大跌时详细一点"
    owner, _, _, _ = seed_job(message=message)
    scoped = draft(
        canonical_key="response_style",
        content_text="detailed",
        scope_type="condition",
        scope={"condition": "市场大跌时"},
        structured={},
        source_quote=message,
        confidence=0.75,
        stability="conditional_scope",
    )
    provider = JudgeCandidateProvider(
        [scoped], {"decision": "approve", "risk": "low", "reason": "明确条件偏好"},
    )
    assert MemoryExtractionWorker(provider=provider).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    active = repository.list_active(owner)
    assert len(active) == 1
    assert (active[0]["value"], active[0]["scope"]) == (
        "detailed", {"condition": "市场大跌时"},
    )
    assert active[0]["activation_kind"] == "safe_auto"
    assert "judge_auto" in (active[0]["activation_reason"] or "")
    assert repository.list_candidates(owner) == []
    assert provider.judge_calls == 1


def test_worker_judge_shadow_records_approval_without_activation(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto_judge_shadow")
    message = "以后市场大跌时详细一点"
    owner, _, _, job_id = seed_job(message=message)
    scoped = draft(
        canonical_key="response_style",
        content_text="detailed",
        scope_type="condition",
        scope={"condition": "市场大跌时"},
        structured={},
        source_quote=message,
        confidence=0.75,
        stability="conditional_scope",
    )
    provider = JudgeCandidateProvider(
        [scoped], {"decision": "approve", "risk": "low", "reason": "明确条件偏好"},
    )
    assert MemoryExtractionWorker(provider=provider).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    assert repository.list_active(owner) == []
    assert len(repository.list_candidates(owner)) == 1
    assert provider.judge_calls == 1
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert len(result["judge_shadow_approved_ids"]) == 1
    assert result["auto_activated_ids"] == []
    assert result["auto_decisions"][0]["outcome"] == "judge_shadow_approved"


def test_worker_safe_auto_shadow_records_eligibility_without_activation(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto_shadow")
    owner, _, _, job_id = seed_job()
    assert MemoryExtractionWorker(provider=CandidateProvider([draft()])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    assert len(repository.list_active(owner)) == 0
    candidates = repository.list_candidates(owner)
    assert len(candidates) == 1
    assert candidates[0]["activation_kind"] == "none"
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert len(result["auto_shadow_eligible_ids"]) == 1
    assert result["auto_activated_ids"] == []


def test_worker_safe_auto_holds_delete_and_other_review_candidates(
    postgresql_database, monkeypatch,
):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "safe_auto")
    owner, _, _, job_id = seed_job()
    active = LongTermMemoryRepository().put(owner, MemoryWrite(
        memory_type="preference", key="response_style", value="concise",
        source_user_message="以后回答简洁",
    ), "active-style")
    deletion = draft(
        candidate_action="delete",
        memory_type="preference",
        canonical_key="response_style",
        content_text="不要再记住回答要简洁",
        scope_type="global",
        scope={},
        structured={},
        source_quote=MESSAGE,
        stability="explicit_correction",
    )
    assert MemoryExtractionWorker(provider=CandidateProvider([deletion])).run_once(limit=1) == 1

    repository = LongTermMemoryRepository()
    assert repository.get(owner, active.current.memory_id).status == "active"
    candidate = repository.list_candidates(owner)
    assert len(candidate) == 1
    assert candidate[0]["activation_kind"] == "none"
    with closing(connect()) as db:
        result = json.loads(db.execute(
            "SELECT result_json FROM memory_extraction_jobs WHERE job_id=?", (job_id,),
        ).fetchone()["result_json"])
    assert result["auto_activated_ids"] == []
    assert result["auto_held_count"] == 1
    assert result["auto_decisions"][0]["outcome"] == "held"
    assert "not_an_upsert" in result["auto_decisions"][0]["reasons"]


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
