"""Versioned, evaluator-only task contracts. Never include these in Agent prompts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUITE_VERSION = "agent-golden-v1.2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(StrictModel):
    statuses: list[str] = Field(default_factory=lambda: ["complete"], min_length=1)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] | None = None
    ordered: bool = False
    table_only: bool = False
    require_evidence: bool = True
    evidence_dates: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    semantic_checks: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(default=8, ge=0)

    @model_validator(mode="after")
    def validate_rows(self):
        if self.rows is not None and not self.columns:
            raise ValueError("Expected table rows require column field names")
        if self.rows is not None and any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every expected row must match the column count")
        return self


class Turn(StrictModel):
    user: str = Field(min_length=1)
    expect: Expectation
    page_date: str | None = None
    page_symbol: str | None = None
    # A new scope starts an isolated session; returning to a prior scope restores it.
    session: str = "main"


class SeedMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    category: Literal["single", "multi", "robustness"]
    family: str
    split: Literal["development", "holdout"] = "development"
    tags: list[str] = Field(min_length=1)
    smoke: bool = False
    source: list[str] = Field(default_factory=list)
    clock: str = "2026-09-22T18:00:00+08:00"
    variant: Literal["normal", "empty", "partial", "error", "truncated", "injection", "stale"] = "normal"
    seed_messages: list[SeedMessage] = Field(default_factory=list)
    memory_seed: dict[str, Any] | None = None
    turns: list[Turn] = Field(min_length=1)
    notes: str = ""


class Check(StrictModel):
    name: str
    passed: bool | None
    detail: str = ""
    expected: Any = None
    actual: Any = None


def verdict(checks: list[Check]) -> str:
    """Unknown judgements cannot become passes; hard failures always win."""
    if any(check.passed is False for check in checks):
        return "fail"
    if not checks or any(check.passed is None for check in checks):
        return "review"
    return "pass"
