"""Versioned evaluation assets, independent of production Agent state."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


Identifier = Annotated[str, Field(min_length=1)]
Version = Annotated[int, Field(ge=1)]
Profile = Literal["v1_close_review", "extended"]
Terminal = Literal["complete", "partial", "empty", "clarify", "refuse", "error", "cancelled"]
Verdict = Literal["pass", "fail", "needs_review", "not_run", "unscorable"]
FailureCause = Literal[
    "agent_failure", "tool_failure", "data_failure", "provider_failure",
    "fixture_failure", "evaluator_failure", "judge_failure",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AssetRef(Contract):
    id: Identifier
    version: Version


class CalendarSpec(Contract):
    id: Identifier
    version: Version
    start_date: date
    end_date: date
    trading_dates: list[date]

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date > self.end_date:
            raise ValueError("calendar start_date must not exceed end_date")
        if self.trading_dates != sorted(set(self.trading_dates)):
            raise ValueError("trading_dates must be sorted and unique")
        if any(not self.start_date <= day <= self.end_date for day in self.trading_dates):
            raise ValueError("trading date outside calendar coverage")
        return self


class ObservationSpec(Contract):
    """Canonical Gateway observation, not a second implementation of tool logic."""

    state: Literal["ok", "empty", "partial", "error"]
    payload: dict[str, JsonValue] | list[JsonValue]
    summary: str
    source_errors: list[str] = Field(default_factory=list)


class RecordingSpec(Contract):
    id: Identifier
    tool: Identifier
    arguments: dict[str, JsonValue]
    observation: ObservationSpec
    origin: Literal["recorded", "derived", "synthetic"]
    provenance: Identifier


class WorldSpec(Contract):
    schema_version: Literal["agent-eval-v1"] = "agent-eval-v1"
    world_id: Identifier
    world_version: Version
    profile: Profile
    anchor_datetime: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    latest_local_trade_date: date | None
    trading_calendar: CalendarSpec
    tool_contract_version: Identifier
    evidence_version: Identifier
    recordings: list[RecordingSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_world(self):
        if self.anchor_datetime.utcoffset() is None:
            raise ValueError("anchor_datetime must include a timezone offset")
        anchor_date = self.anchor_datetime.astimezone(ZoneInfo(self.timezone)).date()
        calendar = self.trading_calendar
        if not calendar.start_date <= anchor_date <= calendar.end_date:
            raise ValueError("anchor date outside calendar coverage")
        if self.latest_local_trade_date is not None:
            if self.latest_local_trade_date not in calendar.trading_dates:
                raise ValueError("latest_local_trade_date must be a declared trading day")
            if self.latest_local_trade_date > anchor_date:
                raise ValueError("latest local data cannot be later than anchor date")
        ids = [record.id for record in self.recordings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate recording ID")
        return self


class TurnSpec(Contract):
    role: Literal["user", "assistant"]
    content: Identifier


class RequirementSpec(Contract):
    id: Identifier
    description: Identifier
    source_turn: Annotated[int, Field(ge=0)]
    source_text: Identifier


class AssertionSpec(Contract):
    """Typed assertion intent; paths are data, never executable expressions."""

    id: Identifier
    evaluator: Literal["trajectory", "compute", "evidence", "fact", "terminal", "safety"]
    kind: Literal[
        "tool_required", "tool_forbidden", "argument_equals", "evidence_current",
        "set_equals", "number_equals", "fact_supported", "disclosure_required",
        "dependency_from_observation", "answer_matches_observation",
    ]
    requirement_id: Identifier
    target: Identifier
    expected: JsonValue = None
    absolute_tolerance: Annotated[float, Field(ge=0)] = 0


class TerminalSpec(Contract):
    allowed_status: list[Terminal] = Field(min_length=1)
    missing_requirement_ids: list[Identifier] = Field(default_factory=list)


class CaseSpec(Contract):
    schema_version: Literal["agent-eval-v1"] = "agent-eval-v1"
    case_id: Identifier
    case_version: Version
    profile: Profile
    mode: Literal["offline", "live_historical", "live_current", "live_external_canary"]
    severity: Literal["P0", "P1", "P2"]
    status: Literal["candidate", "quarantine", "active", "superseded", "archived"]
    capabilities: list[Identifier] = Field(min_length=1)
    world: AssetRef | None = None
    conversation: list[TurnSpec] = Field(min_length=1)
    expected_requirements: list[RequirementSpec] = Field(min_length=1)
    assertions: list[AssertionSpec] = Field(min_length=1)
    expected_terminal: TerminalSpec

    @model_validator(mode="after")
    def validate_case(self):
        if self.mode == "offline" and self.world is None:
            raise ValueError("offline case requires a versioned world")
        if self.mode != "offline" and self.world is not None:
            raise ValueError("live case must not reference a frozen world")
        if self.conversation[-1].role != "user":
            raise ValueError("conversation must end with the evaluated user request")
        ids = [requirement.id for requirement in self.expected_requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement ID")
        for requirement in self.expected_requirements:
            if requirement.source_turn >= len(self.conversation):
                raise ValueError("requirement source_turn does not exist")
            turn = self.conversation[requirement.source_turn]
            if turn.role != "user" or requirement.source_text not in turn.content:
                raise ValueError("requirement must cite original user text")
        assertion_ids = [assertion.id for assertion in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("duplicate assertion ID")
        references = [assertion.requirement_id for assertion in self.assertions]
        references += self.expected_terminal.missing_requirement_ids
        if not set(references) <= set(ids):
            raise ValueError("unknown requirement reference")
        if self.mode in {"live_current", "live_external_canary"} and any(
            assertion.kind in {"set_equals", "number_equals"} for assertion in self.assertions
        ):
            raise ValueError("current live facts must use observation-based assertions")
        return self


class BudgetSpec(Contract):
    max_agent_runs: Annotated[int, Field(gt=0)]
    max_model_calls: Annotated[int, Field(gt=0)]
    max_input_tokens: Annotated[int, Field(gt=0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    max_estimated_cost_usd: Annotated[float, Field(gt=0)]
    max_wall_time_seconds: Annotated[int, Field(gt=0)]


class RunManifest(Contract):
    schema_version: Literal["agent-eval-v1"] = "agent-eval-v1"
    run_id: Identifier
    runtime_version: Identifier
    tool_contract_version: Identifier
    evidence_version: Identifier
    evaluator_version: Identifier
    model: Identifier
    prompt_digest: Identifier
    cases: list[AssetRef] = Field(min_length=1)
    world_digests: dict[str, str]
    budget: BudgetSpec
    worker_count: Annotated[int, Field(gt=0)]


class EvaluationFinding(Contract):
    assertion_id: Identifier
    verdict: Literal["pass", "fail", "needs_review"]
    detail: Identifier


class EvalResult(Contract):
    case: AssetRef
    run_id: Identifier
    verdict: Verdict
    primary_cause: FailureCause | None = None
    findings: list[EvaluationFinding]
    model_calls: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)] | None
    elapsed_seconds: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def reject_false_pass(self):
        if self.verdict == "pass" and (
            not self.findings or any(item.verdict != "pass" for item in self.findings)
        ):
            raise ValueError("pass requires nonempty findings with no failure or abstention")
        return self
