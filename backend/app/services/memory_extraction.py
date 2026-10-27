"""Background memory candidate extraction with an owner-scoped outbox.

The worker never activates a candidate. It extracts stable user preferences from
one committed user message, validates them through the same policy used by the
chat path, and writes ``status='candidate'`` rows for explicit confirmation.
"""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.memory_policy import FORBIDDEN_MEMORY_TERMS
from app.database import begin_write, connect
from app.memory_models import (
    TOOL_NAME_ALIASES,
    MemoryScopeType,
    MemoryWrite,
    normalize_memory_scope,
)
from app.repositories.long_term_memory_repository import (
    LongTermMemoryRepository,
    MemoryConflict,
    MemoryNotFound,
)
from app.services.memory_auto_policy import evaluate_safe_auto_candidate
from app.services.llm_provider import (
    LLMProvider,
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    get_llm_provider,
)


EXTRACTOR_VERSION = "memory-extractor-v2"
AUTO_LEARN_ENV = "LIMITUPLAB_MEMORY_AUTO_LEARN"
AUTO_LEARN_MODES = {"off", "candidate", "safe_auto", "safe_auto_shadow"}
DEFAULT_AUTO_LEARN_MODE = "safe_auto"
JOB_LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 60
MAX_CANDIDATES = 5
MIN_CANDIDATE_CONFIDENCE = 0.7


class MemoryExtractionError(RuntimeError):
    """The extractor could not produce a valid candidate response."""


class MemoryCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: Literal["preference", "research_context"]
    candidate_action: Literal["upsert", "delete"] = "upsert"
    content_text: str = Field(min_length=1, max_length=4000)
    canonical_key: str | None = Field(default=None, max_length=120)
    scope_type: MemoryScopeType = "global"
    scope: dict[str, Any] = Field(default_factory=dict)
    structured: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    stability: Literal[
        "explicit_future", "explicit_correction", "conditional_scope", "other"
    ] = "other"
    source_quote: str = Field(min_length=1, max_length=1000)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def safe_content(self):
        if any(term in self.content_text.casefold() for term in FORBIDDEN_MEMORY_TERMS):
            raise ValueError("Candidate contains forbidden content")
        self.scope = normalize_memory_scope(self.scope_type, self.scope)
        if self.canonical_key and self.canonical_key.startswith("tool_display:"):
            label = self.canonical_key.split(":", 1)[1].strip()
            self.canonical_key = "tool_display:" + TOOL_NAME_ALIASES.get(label, label)
        tool_name = str(self.scope.get("tool_name") or "").strip()
        if self.scope_type == "tool" and not tool_name:
            raise ValueError("Tool candidate requires scope.tool_name")
        # The model sometimes invents dotted canonical keys for display policies.
        # Tool display identity is always driven by the public tool id.
        if (self.candidate_action == "upsert" and self.scope_type == "tool"
                and isinstance(self.structured, dict)
                and "display_columns" in self.structured):
            self.canonical_key = "tool_display:" + tool_name
        if self.candidate_action == "delete":
            if not str(self.canonical_key or "").strip():
                raise ValueError("Delete candidate requires the existing canonical key")
            self.structured = {}
        return self


def auto_learn_mode() -> str:
    raw = (os.getenv(AUTO_LEARN_ENV) or DEFAULT_AUTO_LEARN_MODE).strip().lower()
    return raw if raw in AUTO_LEARN_MODES else DEFAULT_AUTO_LEARN_MODE


def enqueue_memory_extraction_in_connection(
    db,
    *,
    run_id: str,
    owner_id: str,
    session_id: str,
    source_message_id: str,
    task_status: str | None,
    memory_action: Any = None,
) -> str | None:
    """Enqueue one extraction job inside the delivery transaction."""

    if auto_learn_mode() == "off":
        return None
    if task_status not in {"complete", "partial", "empty"} or memory_action:
        return None
    job_id = "memjob_" + uuid4().hex
    db.execute("""INSERT INTO memory_extraction_jobs
        (job_id,owner_id,session_id,run_id,source_message_id,extractor_version)
        VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
        (job_id, owner_id, session_id, run_id, source_message_id, EXTRACTOR_VERSION))
    row = db.execute("""SELECT job_id FROM memory_extraction_jobs
        WHERE source_message_id=? AND extractor_version=?""",
        (source_message_id, EXTRACTOR_VERSION)).fetchone()
    return row["job_id"] if row else None


def _parse_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().casefold() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise MemoryExtractionError("Extractor response must be an object")
    return value


def _parameters() -> dict[str, Any]:
    candidate = {
        "type": "object",
        "properties": {
            "memory_type": {"type": "string", "enum": ["preference", "research_context"]},
            "candidate_action": {"type": "string", "enum": ["upsert", "delete"]},
            "content_text": {"type": "string", "maxLength": 4000},
            "canonical_key": {"type": ["string", "null"], "maxLength": 120},
            "scope_type": {
                "type": "string",
                "enum": ["global", "tool", "intent", "topic", "entity", "condition"],
            },
            "scope": {"type": "object"},
            "structured": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "stability": {
                "type": "string",
                "enum": [
                    "explicit_future", "explicit_correction", "conditional_scope", "other",
                ],
            },
            "source_quote": {"type": "string", "maxLength": 1000},
            "reason": {"type": "string", "maxLength": 500},
        },
        "required": [
            "memory_type", "content_text", "scope_type", "scope", "structured",
            "confidence", "stability", "source_quote", "reason",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "memories": {
                "type": "array",
                "maxItems": MAX_CANDIDATES,
                "items": candidate,
            },
        },
        "required": ["memories"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """你是 LimitUpLab 的长期记忆候选提取器。

任务：从当前用户消息中提取未来会话值得使用的稳定偏好、研究背景，或明确的删除请求。
只使用用户明确表达的内容，不看也不推断 assistant 或工具输出。
每条候选必须：
1. 对未来研究有帮助，而不是一次性行情问题；
2. 是稳定偏好、长期研究范围、明确纠正或明确条件性偏好；
3. 有逐字出现在用户消息中的 source_quote；
4. content_text 独立、完整、简短；
5. 拒绝交易授权、仓位、目标价、收益承诺、密码/密钥、敏感个人信息；
6. 临时要求、寒暄、工具结果、新闻事实、模型推测不保存；
7. 不确定时返回空数组。
tool 作用域使用公开工具 id，如 dragon_tiger_list、stock_kline；
展示偏好放入 structured.display_columns，如 ["name"]。
新增或修改使用 candidate_action="upsert"。
只有用户明确说“忘记/删除/不要再记住”某条 existing_memories 中的记忆时，才使用
candidate_action="delete"；必须逐字复制该记忆的 memory_type、canonical_key、
scope_type 和 scope，content_text 描述删除请求，不能猜测或删除未列出的记忆。
普通否定、临时不要、行情条件不构成删除。
不要激活记忆，只提交候选。"""


def extract_memory_candidates(
    provider: LLMProvider,
    *,
    message: str,
    existing: list[dict[str, Any]] | None = None,
) -> list[MemoryCandidateDraft]:
    """Run one structured extraction call; candidates remain unconfirmed."""

    user_prompt = json.dumps({
        "message": message,
        "existing_memories": [
            {"memory_type": item.get("memory_type"), "key": item.get("key"),
             "canonical_key": item.get("canonical_key"),
             "value": item.get("value"), "scope_type": item.get("scope_type"),
             "scope": item.get("scope")}
            for item in (existing or [])[:50]
        ],
    }, ensure_ascii=False)
    try:
        result = provider.generate_function_call(
            SYSTEM_PROMPT, user_prompt,
            function_name="submit_memory_candidates",
            function_description="Submit stable long-term memory candidates; empty is valid.",
            parameters=_parameters(),
        )
        parsed = _parse_json(result.content)
    except (NativeFunctionCallingUnavailable, NativeFunctionCallingError):
        result = provider.generate(
            SYSTEM_PROMPT + "\nReturn only JSON: {\"memories\": [...]}.",
            user_prompt,
        )
        parsed = _parse_json(result.content)
    except Exception as error:
        raise MemoryExtractionError(type(error).__name__) from error
    raw = parsed.get("memories")
    if not isinstance(raw, list):
        raise MemoryExtractionError("Extractor response has no memories array")
    candidates = []
    for item in raw[:MAX_CANDIDATES]:
        try:
            candidate = MemoryCandidateDraft.model_validate(item)
        except Exception:
            continue
        if candidate.source_quote not in message:
            continue
        candidates.append(candidate)
    return candidates


def candidate_key(candidate: MemoryCandidateDraft) -> str:
    if candidate.canonical_key:
        return candidate.canonical_key
    if candidate.scope_type == "tool":
        tool = str(candidate.scope.get("tool_name") or "").strip()
        if tool:
            return "tool_display:" + tool
    digest = sha256(json.dumps(
        [candidate.memory_type, candidate.content_text, candidate.scope],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:16]
    prefix = "research:" if candidate.memory_type == "research_context" else "preference:"
    return prefix + "auto:" + digest


def candidate_write(candidate: MemoryCandidateDraft, *, message: str, run_id: str) -> MemoryWrite:
    return MemoryWrite(
        memory_type=candidate.memory_type,
        key=candidate_key(candidate),
        value=candidate.content_text,
        canonical_key=candidate.canonical_key,
        scope_type=candidate.scope_type,
        scope=candidate.scope,
        structured=candidate.structured,
        source_quote=candidate.source_quote,
        source_run_id=run_id,
        source_user_message=message,
    )


class MemoryExtractionWorker:
    """Claim and process extraction jobs; never activates a candidate."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        provider: LLMProvider | None = None,
        worker_id: str | None = None,
    ):
        self.database_url = database_url
        self.provider = provider or get_llm_provider()
        self.worker_id = worker_id or "memory-worker-" + uuid4().hex[:12]

    def run_once(self, *, limit: int = 5) -> int:
        if auto_learn_mode() == "off":
            return 0
        processed = 0
        for _ in range(max(1, min(limit, 50))):
            job = self._claim()
            if job is None:
                break
            try:
                self._process(job)
            except Exception as error:  # noqa: BLE001 - job failure is data
                self._fail(job, error)
            processed += 1
        return processed

    def _claim(self):
        with closing(connect(self.database_url)) as db:
            begin_write(db)
            row = db.execute("""UPDATE memory_extraction_jobs SET
                status='processing',attempts=attempts+1,locked_by=?,
                locked_until=clock_timestamp()+? * interval '1 second'
                WHERE job_id=(
                    SELECT job_id FROM memory_extraction_jobs
                    WHERE (status='pending' AND available_at<=clock_timestamp())
                       OR (status='processing' AND locked_until<=clock_timestamp())
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                ) RETURNING *""",
                (self.worker_id, JOB_LEASE_SECONDS)).fetchone()
            db.commit()
            return dict(row) if row else None

    def _source_message(self, job):
        with closing(connect(self.database_url)) as db:
            row = db.execute("""SELECT m.content
                FROM chat_messages AS m
                JOIN chat_sessions AS s ON s.session_id=m.session_id
                WHERE m.message_id=? AND m.run_id=? AND s.owner_id=?""",
                (job["source_message_id"], job["run_id"], job["owner_id"])).fetchone()
            return row["content"] if row else None

    def _process(self, job):
        if auto_learn_mode() == "off":
            self._finish(job, "skipped", {"reason": "auto_learn_off"})
            return
        message = self._source_message(job)
        if not message:
            self._finish(job, "skipped", {"reason": "source_message_missing"})
            return
        repository = LongTermMemoryRepository(self.database_url)
        mode = auto_learn_mode()
        existing = repository.list_active(job["owner_id"], limit=50)
        candidates = extract_memory_candidates(
            self.provider, message=message, existing=existing,
        )
        created, skipped = [], 0
        decisions = {"add": 0, "update": 0, "delete": 0, "refresh": 0, "noop": 0, "replay": 0}
        auto_activated, auto_shadow, auto_errors = [], [], []
        auto_held = 0
        for index, candidate in enumerate(candidates):
            if (candidate.confidence < MIN_CANDIDATE_CONFIDENCE
                    or candidate.stability == "other"):
                skipped += 1
                continue
            write = candidate_write(candidate, message=message, run_id=job["run_id"])
            try:
                result = repository.consolidate_candidate(
                    job["owner_id"], write, f"{job['job_id']}:candidate:{index}",
                    action=candidate.candidate_action,
                )
                decision = result["decision"]
                decisions[decision] = decisions.get(decision, 0) + 1
                if result["superseded_candidate_id"] in created:
                    created.remove(result["superseded_candidate_id"])
                if decision in {"add", "update", "delete"}:
                    created.append(result["candidate_id"])
                else:
                    skipped += 1
                if mode in {"safe_auto", "safe_auto_shadow"} and result["candidate_id"]:
                    evaluation = evaluate_safe_auto_candidate(
                        candidate,
                        key=write.key,
                        message=message,
                        consolidation=result,
                    )
                    if evaluation.eligible:
                        audit = {
                            "candidate_id": result["candidate_id"],
                            "reasons": list(evaluation.reasons),
                            "risk": evaluation.risk,
                        }
                        if mode == "safe_auto_shadow":
                            auto_shadow.append(audit)
                        else:
                            try:
                                repository.confirm_candidate(
                                    job["owner_id"], result["candidate_id"],
                                    f"{job['job_id']}:auto:{index}",
                                    activation_kind="safe_auto",
                                    activation_reason=evaluation.activation_reason,
                                )
                                auto_activated.append(audit)
                                if result["candidate_id"] in created:
                                    created.remove(result["candidate_id"])
                            except (MemoryConflict, MemoryNotFound, ValueError) as error:
                                auto_errors.append({
                                    **audit, "error": type(error).__name__,
                                })
                    else:
                        auto_held += 1
            except (MemoryConflict, MemoryNotFound, ValueError):
                skipped += 1
        self._finish(job, "succeeded", {
            "candidate_ids": created, "created_count": len(created),
            "skipped_count": skipped, "candidate_count": len(candidates),
            "decisions": decisions, "auto_mode": mode,
            "auto_activated_ids": [item["candidate_id"] for item in auto_activated],
            "auto_shadow_eligible_ids": [item["candidate_id"] for item in auto_shadow],
            "auto_held_count": auto_held,
            "auto_errors": auto_errors,
            "auto_policy_version": "safe-auto-v1",
        })

    def _finish(self, job, status, result):
        with closing(connect(self.database_url)) as db:
            begin_write(db)
            db.execute("""UPDATE memory_extraction_jobs SET status=?,result_json=?,
                error_message=NULL,finished_at=clock_timestamp(),locked_by=NULL,locked_until=NULL
                WHERE job_id=?""",
                (status, json.dumps(result, ensure_ascii=False), job["job_id"]))
            db.commit()

    def _fail(self, job, error):
        attempts = int(job.get("attempts") or 1)
        if attempts < MAX_ATTEMPTS:
            with closing(connect(self.database_url)) as db:
                begin_write(db)
                db.execute("""UPDATE memory_extraction_jobs SET status='pending',
                    available_at=clock_timestamp()+? * interval '1 second',
                    error_message=?,locked_by=NULL,locked_until=NULL WHERE job_id=?""",
                    (RETRY_DELAY_SECONDS * attempts, type(error).__name__, job["job_id"]))
                db.commit()
        else:
            self._finish(job, "failed", {"error": type(error).__name__})
