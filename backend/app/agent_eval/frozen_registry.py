"""Parameter-sensitive replay at the production Gateway observation boundary."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
from threading import Lock
from typing import Iterator
from zoneinfo import ZoneInfo

from app.agent_eval.models import RecordingSpec, WorldSpec
from app.agents.query_contract import current_query_reference_date, query_reference_date_override
from app.agents.react_runtime.catalog import arguments_model
from app.agents.react_runtime.evidence import EVIDENCE_VERSION, EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import (
    EXTENDED_AGENT_PROFILE, TOOL_CONTRACT_VERSION, TOOL_SCHEMAS,
    V1_CLOSED_MARKET_TOOL_NAMES,
)
from app.models import AgentChatRequest, AgentToolOutcome, AgentToolTrace


class FrozenFixtureError(ValueError):
    """Replay configuration/match error, never a fabricated empty market result."""


@dataclass(frozen=True)
class LocalDate:
    """Only the date metadata consumed by Gateway and Runtime; not a market event."""

    trade_date: date


class FrozenAgentToolRegistry:
    def __init__(self, world: WorldSpec):
        self._world = world.model_copy(deep=True)
        if world.tool_contract_version != TOOL_CONTRACT_VERSION:
            raise FrozenFixtureError("tool contract version mismatch")
        if world.evidence_version != EVIDENCE_VERSION:
            raise FrozenFixtureError("evidence version mismatch")
        self.profile = world.profile
        self.anchor_date = world.anchor_datetime.astimezone(ZoneInfo(world.timezone)).date()
        self.events = (() if world.latest_local_trade_date is None else
                       (LocalDate(world.latest_local_trade_date),))
        self.enabled_tool_names = frozenset(
            item.name for item in TOOL_SCHEMAS
            if self.profile == EXTENDED_AGENT_PROFILE or item.name in V1_CLOSED_MARKET_TOOL_NAMES
        )
        self._contracts = {item.name: item for item in self.schemas()}
        self._models = {name: arguments_model(item) for name, item in self._contracts.items()}
        self._gateway = ToolGateway(self, EvidenceStore())
        self._recordings: dict[str, RecordingSpec] = {}
        self._semantic_recordings: dict[str, RecordingSpec] = {}
        self._attempts: list[dict] = []
        self._lock = Lock()
        with self.anchored():
            for recording in self._world.recordings:
                try:
                    arguments = self._arguments(recording.tool, recording.arguments)
                except ValueError as error:
                    raise FrozenFixtureError(f"invalid recording {recording.id}: {error}") from error
                errors = (recording.observation.payload.get("source_errors", [])
                          if isinstance(recording.observation.payload, dict) else [])
                if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
                    raise FrozenFixtureError("source_errors must be a list of strings")
                signature = self._signature(recording.tool, arguments)
                if signature in self._recordings:
                    raise FrozenFixtureError("ambiguous recordings for the same effective arguments")
                self._recordings[signature] = recording
                semantic = self._signature(recording.tool, self._semantic_arguments(recording.tool, arguments))
                previous = self._semantic_recordings.get(semantic)
                if previous is not None and previous.observation != recording.observation:
                    raise FrozenFixtureError("conflicting recordings for equivalent production arguments")
                self._semantic_recordings.setdefault(semantic, recording)

    def schemas(self):
        return [item for item in TOOL_SCHEMAS if item.name in self.enabled_tool_names]

    def is_enabled(self, name: str) -> bool:
        return name in self.enabled_tool_names

    @contextmanager
    def anchored(self) -> Iterator["FrozenAgentToolRegistry"]:
        """Wrap the entire Agent Run; production copies this context to tool threads."""
        with query_reference_date_override(self.anchor_date):
            yield self

    @property
    def attempts(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._attempts)

    def _arguments(self, name: str, arguments: dict) -> dict:
        validated = self._gateway.validate({"name": name, "args": arguments})
        if name not in self._models:
            raise ValueError("control tools do not belong to the frozen business registry")
        # Match effective production defaults; explicit null is omitted by _invoke.
        supplied = {key: value for key, value in validated.items() if value is not None}
        canonical = self._models[name].model_validate(supplied).model_dump(mode="json", exclude_none=True)
        canonical.pop("requested_as_of", None)
        if canonical.get("symbols") == []:
            raise ValueError("empty symbols must not become an unrestricted query")
        return canonical

    @staticmethod
    def _signature(name: str, arguments: dict) -> str:
        return json.dumps([name, arguments], sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _semantic_arguments(name: str, arguments: dict) -> dict:
        """Collapse public aliases only where the production method is behaviorally identical."""
        effective = deepcopy(arguments)
        if name != "limit_up_events":
            return effective
        status = effective.get("event_status")
        if status is None:
            status = "broken_intraday" if effective.get("broken_only") else (
                "all" if effective.get("closed_only") is False else "closed"
            )
        effective.pop("broken_only", None)
        effective.pop("closed_only", None)
        effective["event_status"] = status
        sort_by = effective.get("sort_by") or "board_height"
        effective["sort_by"] = sort_by
        effective["sort_order"] = effective.get("sort_order") or (
            "asc" if sort_by == "first_limit_time" else "desc"
        )
        return effective

    def execute_frozen_calls(self, calls: list[dict], *, request: AgentChatRequest) -> dict:
        # Request is part of the Gateway protocol, never used as a lookup shortcut.
        traces, observations = [], []
        for call in calls:
            attempt = {"name": call["name"], "arguments": deepcopy(call["arguments"]),
                       "reference_date": current_query_reference_date().isoformat()}
            try:
                if current_query_reference_date() != self.anchor_date:
                    raise FrozenFixtureError("execute the Agent inside registry.anchored()")
                arguments = self._arguments(call["name"], call["arguments"])
                recording = self._recordings.get(self._signature(call["name"], arguments))
                if recording is None:
                    semantic = self._semantic_arguments(call["name"], arguments)
                    recording = self._semantic_recordings.get(self._signature(call["name"], semantic))
                if recording is None:
                    raise FrozenFixtureError("no recording matches the effective tool arguments")
                observation = recording.observation
                payload = deepcopy(observation.payload)
                # UI traces require objects; keep the native observation separately.
                trace_payload = payload if isinstance(payload, dict) else {"items": payload}
                traces.append(AgentToolTrace(
                    name=call["name"], input=deepcopy(recording.tool_result_input
                        if recording.tool_result_input is not None else call["arguments"]),
                    output=trace_payload, summary=observation.summary,
                    status="error" if observation.state == "error" else "success",
                    result=AgentToolOutcome(status=observation.state, payload=trace_payload,
                                            data_fresh=observation.data_fresh,
                                            source_errors=observation.source_errors),
                ))
                observations.append(payload)
                attempt.update(outcome="matched", recording_id=recording.id)
            except Exception as error:
                attempt.update(outcome="rejected", error_type=type(error).__name__)
                raise
            finally:
                with self._lock:
                    self._attempts.append(attempt)
        return {"tool_results": traces, "observation_payloads": observations}
