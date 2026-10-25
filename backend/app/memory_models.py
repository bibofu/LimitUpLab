"""Public, bounded data contracts for explicitly managed long-term memory."""

from datetime import datetime
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator

MemoryType = Literal["preference", "research_context", "research_archive"]


class MemoryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(default="", max_length=8000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
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

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, metadata):
        if len(json.dumps(metadata, ensure_ascii=False, allow_nan=False)) > 20000:
            raise ValueError("Memory metadata exceeds its size limit")
        return metadata


class MemoryWrite(MemoryContent):
    memory_type: MemoryType
    key: str = Field(min_length=1, max_length=120)

    @field_validator("key")
    @classmethod
    def normalized_key(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Memory key must be a nonblank printable string")
        return value


class MemoryRecord(BaseModel):
    memory_id: str
    owner_id: str
    memory_type: MemoryType
    key: str
    value: str | None
    topics: list[str]
    metadata: dict[str, JsonValue]
    revision: int
    expires_at: datetime | None
    deleted_at: datetime | None
    expired: bool = False
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
