"""Strict, versioned dataset contract for the seven-stage Chat Eval V2."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.capability_contract import available_capability_names
from app.agents.tools import V1_CLOSED_MARKET_TOOL_NAMES


CHAT_EVAL_DATASET_VERSION = "agent-chat-eval-v2"
CHAT_EVAL_FIXTURE_ID = "chat-fixture-v2"
DEV_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "agent_chat_eval_dev_v2.json"
)
HOLDOUT_PATH_ENV = "LIMITUPLAB_EVAL_HOLDOUT_PATH"

DEV_PRIMARY_TYPE_COUNTS = {
    "capability_base": 69,
    "multi_turn": 18,
    "composite": 12,
    "failure": 9,
    "safety": 6,
    "out_of_scope": 6,
}
HOLDOUT_PRIMARY_TYPE_COUNTS = {
    "capability_base": 23,
    "multi_turn": 6,
    "composite": 4,
    "failure": 3,
    "safety": 2,
    "out_of_scope": 2,
}
EXPECTED_DATASET_SIZE = {"dev": 120, "holdout": 40}
RESULT_STATES = frozenset({"ok", "empty", "partial", "error"})


class EvalConversationTurn(BaseModel):
    """One user or assistant message in an evaluation scenario."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class EvalEvidenceClaim(BaseModel):
    """One relation-aware fact expected in evidence or the visible answer."""

    model_config = ConfigDict(extra="forbid")

    entity: str | None = None
    date: str | None = None
    metric: str
    value: str | int | float | bool | None
    source_path: str
    critical: bool = False


class EvalPolicyExpectation(BaseModel):
    """Expected policy behavior after the raw planner decision."""

    model_config = ConfigDict(extra="forbid")

    repair_needed: bool = False
    required_repairs: list[str] = Field(default_factory=list)
    forbidden_repairs: list[str] = Field(default_factory=list)


class EvalAnswerAssertions(BaseModel):
    """Deterministic constraints for the rendered answer."""

    model_config = ConfigDict(extra="forbid")

    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    required_symbols: list[str] = Field(default_factory=list)
    forbidden_symbols: list[str] = Field(default_factory=list)
    ordered_symbols: list[str] = Field(default_factory=list)
    max_chars: int = Field(default=2400, ge=1, le=10000)


class EvalExpectedBehavior(BaseModel):
    """All seven-stage gold expectations for one case."""

    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any] = Field(default_factory=dict)
    allowed_capability_sets: list[list[str]] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    tool_parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    policy_repairs: EvalPolicyExpectation = Field(
        default_factory=EvalPolicyExpectation
    )
    result_states: dict[str, str] = Field(default_factory=dict)
    evidence_claims: list[EvalEvidenceClaim] = Field(default_factory=list)
    response_behavior: Literal[
        "answer", "clarify", "empty_disclosure", "refuse"
    ] = "answer"
    answer_assertions: EvalAnswerAssertions = Field(
        default_factory=EvalAnswerAssertions
    )

    @model_validator(mode="after")
    def validate_tool_contract(self) -> "EvalExpectedBehavior":
        overlap = set(self.required_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(f"tools cannot be both required and forbidden: {sorted(overlap)}")
        unknown_states = set(self.result_states.values()) - RESULT_STATES
        if unknown_states:
            raise ValueError(f"unsupported result states: {sorted(unknown_states)}")
        if set(self.tool_parameters) - set(self.required_tools):
            raise ValueError("tool parameters may only target required tools")
        if set(self.result_states) - set(self.required_tools):
            raise ValueError("result states may only target required tools")
        return self


class ChatEvalCase(BaseModel):
    """One auditable single- or multi-turn Chat Eval scenario."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    dataset: Literal["dev", "holdout"]
    profile: Literal["v1_close_review"] = "v1_close_review"
    severity: Literal["critical", "high", "normal"] = "normal"
    primary_type: Literal[
        "capability_base",
        "multi_turn",
        "composite",
        "failure",
        "safety",
        "out_of_scope",
    ]
    tags: list[str] = Field(default_factory=list)
    fixture_snapshot_id: str
    anchor_datetime: datetime
    conversation: list[EvalConversationTurn] = Field(min_length=1, max_length=7)
    expected: EvalExpectedBehavior
    bad_case_id: str | None = None

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not re.fullmatch(r"CEV2-[DH]\d{3}", value):
            raise ValueError("case_id must match CEV2-Dnnn or CEV2-Hnnn")
        return value

    @model_validator(mode="after")
    def validate_case_shape(self) -> "ChatEvalCase":
        if self.fixture_snapshot_id != CHAT_EVAL_FIXTURE_ID:
            raise ValueError(
                f"fixture_snapshot_id must be {CHAT_EVAL_FIXTURE_ID}"
            )
        if self.anchor_datetime.utcoffset() is None:
            raise ValueError("anchor_datetime must include a timezone")
        expected_prefix = "CEV2-D" if self.dataset == "dev" else "CEV2-H"
        if not self.case_id.startswith(expected_prefix):
            raise ValueError("case_id prefix does not match dataset")
        if self.primary_type == "multi_turn":
            user_turns = sum(turn.role == "user" for turn in self.conversation)
            if user_turns < 2:
                raise ValueError("multi_turn cases require at least two user turns")
        return self


class ChatEvalDataset(BaseModel):
    """Top-level artifact containing one public or private dataset split."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["agent-chat-eval-v2"]
    dataset: Literal["dev", "holdout"]
    description: str
    cases: list[ChatEvalCase]


def load_chat_eval_dataset(path: Path, *, expected_split: str) -> ChatEvalDataset:
    """Load and validate one exact V2 split without silently accepting drift."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    dataset = ChatEvalDataset.model_validate(payload)
    if dataset.dataset != expected_split:
        raise ValueError(
            f"expected {expected_split} dataset, got {dataset.dataset}"
        )
    expected_size = EXPECTED_DATASET_SIZE[expected_split]
    if len(dataset.cases) != expected_size:
        raise ValueError(
            f"{expected_split} dataset must contain exactly {expected_size} cases"
        )
    _validate_dataset_distribution(dataset)
    return dataset


def load_dev_dataset(path: Path | None = None) -> ChatEvalDataset:
    """Load the committed 120-case developer Golden dataset."""

    return load_chat_eval_dataset(path or DEV_DATASET_PATH, expected_split="dev")


def load_holdout_dataset(path: Path | None = None) -> ChatEvalDataset:
    """Load the private 40-case release holdout from its explicit environment path."""

    raw_path = str(path or os.getenv(HOLDOUT_PATH_ENV, "")).strip()
    if not raw_path:
        raise ValueError(f"{HOLDOUT_PATH_ENV} is required for holdout evaluation")
    resolved = Path(raw_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"holdout dataset not found: {resolved}")
    return load_chat_eval_dataset(resolved, expected_split="holdout")


def load_dataset_selection(selection: str) -> list[ChatEvalCase]:
    """Resolve the CLI dataset selector while keeping Holdout private by default."""

    if selection == "dev":
        return list(load_dev_dataset().cases)
    if selection == "holdout":
        return list(load_holdout_dataset().cases)
    if selection == "all":
        dev = load_dev_dataset().cases
        holdout = load_holdout_dataset().cases
        normalized = [_conversation_key(case) for case in [*dev, *holdout]]
        if len(normalized) != len(set(normalized)):
            raise ValueError("dev and holdout contain duplicate conversations")
        return [*dev, *holdout]
    raise ValueError(f"unsupported dataset selection: {selection}")


def _validate_dataset_distribution(dataset: ChatEvalDataset) -> None:
    expected_counts = (
        DEV_PRIMARY_TYPE_COUNTS
        if dataset.dataset == "dev"
        else HOLDOUT_PRIMARY_TYPE_COUNTS
    )
    actual_counts = Counter(case.primary_type for case in dataset.cases)
    if dict(actual_counts) != expected_counts:
        raise ValueError(
            f"{dataset.dataset} primary-type distribution must be {expected_counts}, "
            f"got {dict(actual_counts)}"
        )
    ids = [case.case_id for case in dataset.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    conversations = [_conversation_key(case) for case in dataset.cases]
    if len(conversations) != len(set(conversations)):
        raise ValueError("normalized conversations must be unique")

    available = set(available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES))
    observed: Counter[str] = Counter()
    for case in dataset.cases:
        for capability_set in case.expected.allowed_capability_sets:
            unknown = set(capability_set) - available
            if unknown:
                raise ValueError(
                    f"{case.case_id} references unavailable capabilities: {sorted(unknown)}"
                )
        if case.primary_type == "capability_base":
            if len(case.expected.allowed_capability_sets) != 1:
                raise ValueError(
                    f"{case.case_id} capability_base requires one exact capability set"
                )
            capability_set = case.expected.allowed_capability_sets[0]
            if len(capability_set) != 1:
                raise ValueError(
                    f"{case.case_id} capability_base requires one capability"
                )
            observed[capability_set[0]] += 1
    expected_per_capability = 3 if dataset.dataset == "dev" else 1
    expected_coverage = Counter(
        {name: expected_per_capability for name in sorted(available)}
    )
    if observed != expected_coverage:
        raise ValueError(
            f"{dataset.dataset} capability coverage must be {dict(expected_coverage)}, "
            f"got {dict(observed)}"
        )


def _conversation_key(case: ChatEvalCase) -> str:
    return "|".join(
        re.sub(r"\s+", "", turn.content).lower()
        for turn in case.conversation
        if turn.role == "user"
    )
