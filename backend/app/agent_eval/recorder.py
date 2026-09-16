"""Record canonical tool observations and verify replay without model calls."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from pydantic import Field, JsonValue, model_validator

from app.agent_eval.frozen_registry import FrozenAgentToolRegistry, FrozenFixtureError
from app.agent_eval.models import CalendarSpec, Contract, ObservationSpec, Profile, RecordingSpec, WorldSpec
from app.agents.query_contract import query_reference_date_override
from app.agents.react_runtime.evidence import EVIDENCE_VERSION, EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_CONTRACT_VERSION


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def structure(value: JsonValue) -> JsonValue:
    """Keep every observed row shape; values and list ordering do not affect shape."""
    if isinstance(value, dict):
        return {"object": {key: structure(item) for key, item in sorted(value.items())}}
    if isinstance(value, list):
        variants = {canonical_json(structure(item)) for item in value}
        return {"array": [json.loads(item) for item in sorted(variants)]}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def stable_view(view: dict) -> dict:
    # These two fields identify an execution, not market facts. Retain all others.
    return {key: value for key, value in view.items() if key not in {"evidence_id", "retrieved_at"}}


class CaptureBody(Contract):
    schema_version: Literal["agent-capture-v1"] = "agent-capture-v1"
    profile: Profile
    anchor_datetime: datetime
    captured_at: datetime
    tool_contract_version: str
    evidence_version: str
    recording: RecordingSpec
    raw_tool_result: dict[str, JsonValue]
    evidence_view: dict[str, JsonValue]
    source_manifest: dict[str, JsonValue]
    privacy_status: Literal["unreviewed"] = "unreviewed"

    @model_validator(mode="after")
    def require_aware_times(self):
        if self.anchor_datetime.utcoffset() is None or self.captured_at.utcoffset() is None:
            raise ValueError("capture timestamps must be timezone-aware")
        return self


class CaptureArtifact(Contract):
    body: CaptureBody
    checksum: str
    structure_digests: dict[str, str] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def verify_integrity(self):
        if self.checksum != digest(self.body.model_dump(mode="json")):
            raise ValueError("capture checksum mismatch")
        if self.structure_digests != fingerprints(self.body):
            raise ValueError("capture structure fingerprint mismatch")
        return self


def fingerprints(body: CaptureBody) -> dict[str, str]:
    return {
        "raw_tool_result": digest(structure(body.raw_tool_result)),
        "payload": digest(structure(body.recording.observation.payload)),
        "evidence_view": digest(structure(body.evidence_view)),
    }


def capture_tool(registry, *, tool: str, arguments: dict, anchor_datetime: datetime,
                 recording_id: str, provenance: str, source_manifest: dict) -> CaptureArtifact:
    """Invoke once through production Gateway. Caller owns source access/isolation."""
    if callable(getattr(registry, "execute_frozen_calls", None)):
        raise FrozenFixtureError("cannot label a frozen replay as a real tool recording")
    if anchor_datetime.utcoffset() is None:
        raise ValueError("anchor_datetime must be timezone-aware")
    store = EvidenceStore()
    gateway = ToolGateway(registry, store)
    with query_reference_date_override(anchor_datetime.astimezone(ZoneInfo("Asia/Shanghai")).date()):
        validated = gateway.validate({"name": tool, "args": arguments})
        result, payload, state = gateway.execute(tool, validated)
    if not isinstance(payload, (dict, list)):
        raise FrozenFixtureError("Gateway observation must be an object or list for replay")
    # Capture input at Gateway boundary separately from any normalized ToolResult input.
    observation = ObservationSpec(state=state, payload=payload, summary=result.summary,
                                  data_fresh=result.data_fresh,
                                  source_errors=list(result.source_errors))
    evidence_id = store.add(tool=tool, payload=payload, state=state, arguments=result.input)
    body = CaptureBody(
        profile=registry.profile, anchor_datetime=anchor_datetime,
        captured_at=datetime.now(timezone.utc), tool_contract_version=TOOL_CONTRACT_VERSION,
        evidence_version=EVIDENCE_VERSION,
        recording=RecordingSpec(id=recording_id, tool=tool, arguments=validated,
                                observation=observation, origin="recorded", provenance=provenance,
                                tool_result_input=jsonable_encoder(result.input)),
        raw_tool_result=jsonable_encoder({
            "name": result.name, "input": result.input, "output": result.output,
            "trace_output": result.trace_output, "summary": result.summary,
            "status": result.status, "error": result.error,
            "result_status": result.result_status, "data_fresh": result.data_fresh,
            "source_errors": list(result.source_errors),
        }),
        evidence_view=store.view(evidence_id), source_manifest=source_manifest,
    )
    return CaptureArtifact(body=body, checksum=digest(body.model_dump(mode="json")),
                           structure_digests=fingerprints(body))


def save_capture(artifact: CaptureArtifact, destination: Path) -> None:
    """Write a local unreviewed artifact exclusively; never overwrite prior evidence."""
    verified = CaptureArtifact.model_validate_json(artifact.model_dump_json())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(verified.model_dump_json(indent=2) + "\n")


def load_capture(path: Path) -> CaptureArtifact:
    return CaptureArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def verify_replay(artifact: CaptureArtifact, *, calendar: CalendarSpec,
                  latest_local_trade_date) -> dict:
    artifact = CaptureArtifact.model_validate_json(artifact.model_dump_json())
    body = artifact.body
    world = WorldSpec(
        world_id="capture-verification", world_version=1, profile=body.profile,
        anchor_datetime=body.anchor_datetime, latest_local_trade_date=latest_local_trade_date,
        trading_calendar=calendar, tool_contract_version=body.tool_contract_version,
        evidence_version=body.evidence_version, recordings=[body.recording],
    )
    registry = FrozenAgentToolRegistry(world)
    store = EvidenceStore()
    gateway = ToolGateway(registry, store)
    with registry.anchored():
        args = gateway.validate({"name": body.recording.tool, "args": body.recording.arguments})
        result, payload, state = gateway.execute(body.recording.tool, args)
    key = store.add(tool=body.recording.tool, payload=payload, state=state, arguments=result.input)
    actual_view = store.view(key)
    checks = {
        "payload_equal": payload == body.recording.observation.payload,
        "state_equal": state == body.recording.observation.state,
        "source_errors_equal": list(result.source_errors) == body.recording.observation.source_errors,
        "data_fresh_equal": result.data_fresh == body.recording.observation.data_fresh,
        "evidence_view_equal": stable_view(actual_view) == stable_view(body.evidence_view),
        "payload_structure_equal": digest(structure(payload)) == artifact.structure_digests["payload"],
        "view_structure_equal": digest(structure(actual_view)) == artifact.structure_digests["evidence_view"],
    }
    return {"passed": all(checks.values()), "checks": checks, "checksum": artifact.checksum}
