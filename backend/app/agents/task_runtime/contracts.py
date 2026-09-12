"""Validated task contracts; natural language never executes as code."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    description: str
    source_text: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class EvidenceBinding(BaseModel):
    """Bounded field traversal, never arbitrary JSONPath or expression evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_step: str
    path: str = Field(pattern=r"^[A-Za-z0-9_*]+(?:\.[A-Za-z0-9_*]+)*$")
    target_argument: str
    fan_out: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class Predicate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(pattern=r"^[A-Za-z0-9_*]+(?:\.[A-Za-z0-9_*]+)*$")
    operator: Literal["equals", "not_equals", "contains", "greater_than", "less_than", "empty", "not_empty"]
    value: str | float | int | bool | None = None


class TaskStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_id: str
    capability: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    operation: Literal["intersection", "select"] | None = None
    step_type: Literal["tool", "operation"] = "tool"
    requirement_ids: tuple[str, ...]
    bindings: tuple[EvidenceBinding, ...] = Field(default=(), max_length=8)
    # Applied to a successful result list before downstream binding.
    select_path: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_*]+(?:\.[A-Za-z0-9_*]+)*$")
    take: int | None = Field(default=None, ge=1, le=100)
    when_step: str | None = None
    when_states: tuple[Literal["ok", "empty", "partial", "error"], ...] = ()
    when_predicate: Predicate | None = None
    filters: tuple[Predicate, ...] = Field(default=(), max_length=8)
    sort_field: str | None = Field(default=None, description="Field relative to EACH row, e.g. score, NOT collection.*.score")
    sort_descending: bool = True


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[Requirement] = Field(min_length=1, max_length=20)
    steps: list[TaskStep] = Field(default_factory=list, max_length=24)
    behavior: Literal["execute", "clarify", "refuse", "answer"] = "execute"
    message: str = ""

    @model_validator(mode="after")
    def validate_graph(self):
        validate_steps(self.steps, {r.id for r in self.requirements})
        if len({r.id for r in self.requirements}) != len(self.requirements):
            raise ValueError("duplicate requirement ID")
        if self.behavior == "execute" and not self.steps:
            raise ValueError("execution plan has no steps")
        return self


def validate_steps(steps, requirement_ids, existing_ids=()):
    seen = set(existing_ids)
    for step in steps:
        if step.step_id in seen:
            raise ValueError("duplicate step ID")
        if not step.requirement_ids or not set(step.requirement_ids) <= requirement_ids:
            raise ValueError("unknown or missing requirement")
        references = set(step.depends_on) | {b.source_step for b in step.bindings}
        if step.when_step:
            references.add(step.when_step)
        if not references <= seen:
            raise ValueError("dependency must reference an earlier step")
        if bool(step.when_step) != bool(step.when_states or step.when_predicate):
            raise ValueError("conditional steps need both source and states")
        if step.step_type == "tool" and (not step.tool_name or not step.capability):
            raise ValueError("tool steps require tool and capability")
        if step.step_type == "operation" and (step.tool_name or step.capability or step.operation is None):
            raise ValueError("operation steps require an operation and no tool")
        if step.operation == "select" and (len(step.depends_on) != 1 or not step.select_path):
            raise ValueError("select requires one dependency and a collection path")
        if step.operation == "intersection" and len(step.depends_on) != 2:
            raise ValueError("intersection requires two dependencies")
        seen.add(step.step_id)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool
    satisfied_ids: list[str] = Field(default_factory=list)
    missing: dict[str, str] = Field(default_factory=dict)
    reason: str
    can_recover: bool = True
    # Completion and drafting share one observation pass; prose is validated later.
    answer: dict[str, Any] | None = None


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_steps: list[TaskStep] = Field(default_factory=list, max_length=12)
    reason: str


def values_at(payload, path: str):
    """Select fields, integer indexes and wildcards without evaluating expressions."""
    import re
    # Common JSON field notation is just syntax sugar, not arbitrary JSONPath.
    path = re.sub(r"\[(\d+|\*)\]", r".\1", path.removeprefix("$."))
    if not path or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_*." for c in path):
        raise ValueError("invalid evidence path")
    values = [payload]
    for part in path.split("."):
        next_values = []
        for value in values:
            if part == "*" and isinstance(value, list):
                next_values.extend(value)
            elif isinstance(value, dict) and part in value:
                next_values.append(value[part])
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                next_values.append(value[int(part)])
        values = next_values
    return values


def evidence_values(record, path):
    """Explicit record namespaces, with unqualified paths relative to payload."""
    if path.startswith(("payload.", "selected.", "calls.")) or path == "state":
        return values_at(record, path)
    return values_at(record["payload"], path)
