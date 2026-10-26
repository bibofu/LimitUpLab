"""Public, bounded data contracts for explicitly managed long-term memory."""

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

MemoryType = Literal["preference", "research_context", "research_archive"]
MemoryScopeType = Literal["global", "tool", "intent", "topic", "entity", "condition"]

TOOL_NAME_ALIASES = {
    "龙虎榜": "dragon_tiger_list",
    "个股新闻": "stock_news",
    "新闻": "stock_news",
    "k线": "stock_kline",
    "K线": "stock_kline",
    "财报": "stock_financials",
    "公司资料": "stock_profile",
    "热股榜": "hot_stock_ranking",
}


def normalize_memory_scope(
    scope_type: MemoryScopeType, scope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Normalize common Chinese tool labels to canonical public tool ids."""

    if scope_type != "tool" or not isinstance(scope, dict):
        return scope
    normalized = dict(scope)
    tool = (
        normalized.get("tool_name")
        or normalized.get("tool_id")
        or normalized.get("tool")
    )
    if tool is not None:
        text = str(tool).strip()
        normalized["tool_name"] = TOOL_NAME_ALIASES.get(text, text)
        normalized.pop("tool", None)
        normalized.pop("tool_id", None)
    return normalized


def memory_scope_key(scope_type: MemoryScopeType, scope: dict[str, JsonValue]) -> str:
    """Return a deterministic, indexed identity for a scope."""

    if scope_type == "global":
        return "global"
    canonical = json.dumps(scope, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    if len(canonical) <= 120:
        return f"{scope_type}:{canonical}"
    return f"{scope_type}:sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


class MemoryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(default="", max_length=8000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    scope_type: MemoryScopeType = "global"
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    canonical_key: str | None = Field(default=None, max_length=120)
    structured: dict[str, JsonValue] = Field(default_factory=dict)
    source_quote: str | None = Field(default=None, max_length=8000)
    expected_revision: int = Field(default=0, ge=0)
    expires_at: AwareDatetime | None = None
    source_run_id: str | None = Field(default=None, max_length=100)
    source_user_message: str = Field(min_length=1, max_length=8000)
    restore_deleted: bool = False

    @field_validator("source_user_message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Explicit source message must not be blank")
        return value.strip()

    @field_validator("topics")
    @classmethod
    def bounded_topics(cls, topics):
        if any(not item.strip() or len(item) > 100 for item in topics):
            raise ValueError("Topics must contain 1 to 100 characters")
        return list(dict.fromkeys(item.strip() for item in topics))

    @field_validator("metadata", "structured")
    @classmethod
    def bounded_metadata(cls, metadata):
        if len(json.dumps(metadata, ensure_ascii=False, allow_nan=False)) > 20000:
            raise ValueError("Memory metadata exceeds its size limit")
        return metadata

    @field_validator("canonical_key")
    @classmethod
    def normalized_canonical_key(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Canonical key must be a nonblank printable string")
        return value

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope_type == "global":
            if self.scope:
                raise ValueError("Global memory cannot carry a scope object")
            return self
        if not isinstance(self.scope, dict) or not self.scope:
            raise ValueError("Scoped memory requires a non-empty scope object")
        self.scope = normalize_memory_scope(self.scope_type, self.scope)
        required = {
            "tool": "tool_name",
            "intent": "intent",
            "topic": "topic",
            "entity": "symbol",
            "condition": "condition",
        }[self.scope_type]
        if not str(self.scope.get(required) or "").strip():
            raise ValueError(f"Scoped memory requires scope.{required}")
        if len(json.dumps(self.scope, ensure_ascii=False, allow_nan=False)) > 4000:
            raise ValueError("Memory scope exceeds its size limit")
        return self


class MemoryWrite(MemoryContent):
    memory_type: MemoryType
    key: str = Field(min_length=1, max_length=120)

    @field_validator("key")
    @classmethod
    def normalized_key(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Memory key must be a nonblank printable string")
        if value.startswith("tool_display:"):
            label = value.split(":", 1)[1].strip()
            value = "tool_display:" + TOOL_NAME_ALIASES.get(label, label)
        return value


class MemoryRecord(BaseModel):
    memory_id: str
    owner_id: str
    memory_type: MemoryType
    key: str
    value: str | None
    topics: list[str]
    metadata: dict[str, JsonValue]
    scope_type: MemoryScopeType = "global"
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    scope_key: str = "global"
    canonical_key: str | None = None
    structured: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal[
        "candidate", "active", "superseded", "revoked", "expired", "deleted"
    ] = "active"
    revision: int
    management_revision: int = 0
    expires_at: datetime | None
    deleted_at: datetime | None
    expired: bool = False
    source_kind: Literal["legacy", "explicit", "auto", "import"] = "explicit"
    source_quote: str | None = None
    content_hash: str | None = None
    source_run_id: str | None
    source_user_message: str
    created_at: datetime
    updated_at: datetime


class MemoryMutationResult(BaseModel):
    current: MemoryRecord
    operation_revision: int
    owner_revision: int
    context_fingerprint: str
    replayed: bool = False
