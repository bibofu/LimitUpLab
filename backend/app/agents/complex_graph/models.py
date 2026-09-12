"""Typed contracts shared by the bounded complex-query graph."""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.models import AgentToolTrace


class ResultReference(BaseModel):
    """A constrained reference to a named entity set produced by a prior step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_step: str
    entity_set: str
    path: Literal["symbols"] = "symbols"
    max_items: int = Field(default=20, ge=1, le=20)


class ArgumentBinding(BaseModel):
    """Bind one downstream argument from a validated result reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_argument: str
    reference: ResultReference
    fan_out: bool = False
    batch: bool = False


class ComplexPlanStep(BaseModel):
    """One capability-backed tool step or deterministic set operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    capability: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    operation: Literal["intersection"] | None = None
    output_entity_set: str | None = None
    argument_bindings: tuple[ArgumentBinding, ...] = ()
    step_type: Literal["tool", "operation"] = "tool"
    replan_index: int = 0


class CompletionCheck(BaseModel):
    """Deterministic decision made after observing the current evidence."""

    complete: bool
    missing_requirements: list[str] = Field(default_factory=list)
    reason: str


class ReplanRequest(BaseModel):
    """Bounded input made available to a replanner."""

    original_user_query: str
    original_plan: list[dict[str, Any]]
    completed_steps: list[str]
    failed_steps: list[str]
    tool_observations: list[dict[str, Any]]
    missing_requirements: list[str]
    remaining_tool_calls: int
    remaining_replans: int


class ReplanOutput(BaseModel):
    """Structured replacement steps; free-text plans are never executable."""

    new_steps: list[ComplexPlanStep] = Field(default_factory=list)
    reason: str


class EntityRef(TypedDict):
    symbol: str
    name: str | None
    source_steps: list[str]


class ComplexAgentState(TypedDict):
    graph_run_id: str
    scenario: str
    user_query: str
    messages: list[dict[str, Any]]
    plan_steps: list[dict[str, Any]]
    current_step: str | None
    entity_sets: dict[str, list[EntityRef]]
    facts: dict[str, Any]
    tool_results: list[AgentToolTrace]
    graph_traces: list[AgentToolTrace]
    completed_steps: list[str]
    failed_steps: list[str]
    tool_call_names: list[str]
    references: list[str]
    tool_call_count: int
    llm_call_count: int
    final_answer: str
    answer_meta: dict[str, Any]
    completion_status: str
    failure_reason: str | None
    completion_check: dict[str, Any]
    replan_count: int
    replan_traces: list[dict[str, Any]]
    graph_compilation_count: int
    backend_repair_count: int
    policy_repair_count: int
    successful_call_fingerprints: list[str]
