"""Recorder integrity, observed representation and replay fidelity."""

from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent_eval.models import CalendarSpec
from app.agent_eval.recorder import (
    CaptureArtifact, capture_tool, digest, load_capture, save_capture, structure, verify_replay,
)
from app.agents.tools import TOOL_SCHEMAS, ToolResult


ANCHOR = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
CALENDAR = CalendarSpec(id="synthetic", version=1, start_date=date(2026, 9, 11),
                        end_date=date(2026, 9, 11), trading_dates=[date(2026, 9, 11)])


def capture(state="ok", payload=None, errors=(), fresh=None):
    payload = payload if payload is not None else {"count": 40, "trade_date": "2026-09-11"}
    def market_summary(*, include_limit_down=False):
        return ToolResult(name="market_summary", input={"include_limit_down": include_limit_down},
                          output=payload, trace_output=payload, summary="synthetic test",
                          result_status=state, source_errors=errors, data_fresh=fresh)
    registry = SimpleNamespace(profile="v1_close_review", events=[SimpleNamespace(trade_date=ANCHOR.date())],
        schemas=lambda: [item for item in TOOL_SCHEMAS if item.name == "market_summary"],
        is_enabled=lambda name: name == "market_summary", market_summary=market_summary)
    return capture_tool(registry, tool="market_summary", arguments={}, anchor_datetime=ANCHOR,
                        recording_id="test", provenance="synthetic function for recorder contract test",
                        source_manifest={"test_only": True})


@pytest.mark.parametrize("state", ["ok", "empty", "partial", "error"])
def test_record_replay_preserves_states_and_effective_input(state):
    artifact = capture(state, errors=("source unavailable",) if state == "error" else ())
    assert artifact.body.recording.arguments == {}
    assert artifact.body.raw_tool_result["input"] == {"include_limit_down": False}
    assert verify_replay(artifact, calendar=CALENDAR, latest_local_trade_date=ANCHOR.date())["passed"]


def test_count_only_partial_metadata_and_visible_paths_survive():
    payload = {"items": [], "matched_count": 40, "result_mode": "count", "source": "test"}
    artifact = capture(payload=payload)
    assert artifact.body.evidence_view["metadata"]["matched_count"] == 40
    assert artifact.body.evidence_view["source_truncated"] is False
    assert verify_replay(artifact, calendar=CALENDAR, latest_local_trade_date=ANCHOR.date())["passed"]
    partial = capture(payload={**payload, "data_missing": ["secondary source"]}, fresh=False)
    assert partial.body.evidence_view["result_state"] == "partial"
    assert verify_replay(partial, calendar=CALENDAR, latest_local_trade_date=ANCHOR.date())["passed"]


def test_full_payload_not_replaced_by_preview():
    artifact = capture(payload={"items": [{"symbol": str(i)} for i in range(40)]})
    assert len(artifact.body.recording.observation.payload["items"]) == 40
    assert len(artifact.body.evidence_view["rows"]) == 8
    assert verify_replay(artifact, calendar=CALENDAR, latest_local_trade_date=ANCHOR.date())["passed"]


@pytest.mark.parametrize("part", ["payload", "raw", "view", "fingerprint"])
def test_tampering_detected_without_running_a_tool(part):
    data = capture().model_dump(mode="json")
    if part == "payload":
        data["body"]["recording"]["observation"]["payload"]["count"] = 99
    elif part == "raw":
        data["body"]["raw_tool_result"]["output"]["count"] = 99
    elif part == "view":
        data["body"]["evidence_view"]["row_count"] = 99
    else:
        data["structure_digests"]["payload"] = "invalid"
    with pytest.raises(ValidationError):
        CaptureArtifact.model_validate(data)


def test_fingerprints_detect_missing_and_changed_type_across_all_rows():
    original = [{"symbol": "a", "count": 2}, {"symbol": "b", "count": 3}]
    reordered = list(reversed(original))
    assert structure(original) == structure(reordered)
    changed = deepcopy(original)
    changed[1]["count"] = "3"
    assert structure(original) != structure(changed)
    del changed[1]["count"]
    assert structure(original) != structure(changed)
    assert structure(True) != structure(1)


def test_save_load_no_overwrite_and_mutation_revalidation(tmp_path):
    path = tmp_path / "capture.json"
    artifact = capture()
    save_capture(artifact, path)
    assert load_capture(path).checksum == artifact.checksum
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_capture(artifact, path)
    assert path.read_bytes() == before
    artifact.body.recording.observation.payload["count"] = 999
    with pytest.raises(ValidationError):
        save_capture(artifact, tmp_path / "mutated.json")
    assert not (tmp_path / "mutated.json").exists()


def test_replay_verifier_catches_self_consistent_but_wrong_view():
    data = capture().model_dump(mode="json")
    data["body"]["evidence_view"]["metadata"]["count"] = 99
    data["checksum"] = digest(data["body"])
    artifact = CaptureArtifact.model_validate(data)
    report = verify_replay(artifact, calendar=CALENDAR, latest_local_trade_date=ANCHOR.date())
    assert not report["passed"]
    assert not report["checks"]["evidence_view_equal"]


def test_local_recording_reads_exact_date_without_changing_database(tmp_path):
    from app.agent_eval.local_capture import record_local_summary
    from app.repositories.limit_up_repository import SQLiteLimitUpRepository
    from app.services.sample_data import SAMPLE_EVENTS

    database = tmp_path / "source.sqlite"
    events = [event.model_copy(update={"trade_date": ANCHOR.date()}) for event in SAMPLE_EVENTS]
    SQLiteLimitUpRepository(database).replace_events(events)
    before = database.read_bytes()
    destination = tmp_path / "recording.json"
    report = record_local_summary(database, ANCHOR, destination)
    assert report["passed"]
    assert report["source_rows"] == len(events)
    assert report["limit_up_count"] == sum(event.closed_limit for event in events)
    assert database.read_bytes() == before
    assert load_capture(destination).body.source_manifest["trade_date"] == "2026-09-11"
    with pytest.raises(ValueError, match="no local events"):
        record_local_summary(database, ANCHOR.replace(day=12), tmp_path / "missing.json")
    assert not (tmp_path / "missing.json").exists()
    assert database.read_bytes() == before


def test_local_recording_never_creates_source_database(tmp_path):
    import sqlite3
    from app.agent_eval.local_capture import LocalSummaryRegistry, record_local_summary

    database = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        record_local_summary(database, ANCHOR, tmp_path / "capture.json")
    assert not database.exists()
    with pytest.raises(ValueError, match="remote"):
        LocalSummaryRegistry([]).market_summary(include_limit_down=True)


def test_capture_rejects_frozen_sources_and_naive_anchors():
    from app.agent_eval.frozen_registry import FrozenFixtureError

    kwargs = dict(tool="market_summary", arguments={}, anchor_datetime=ANCHOR,
                  recording_id="test", provenance="test", source_manifest={})
    with pytest.raises(FrozenFixtureError, match="frozen replay"):
        capture_tool(SimpleNamespace(execute_frozen_calls=lambda: None), **kwargs)
    kwargs["anchor_datetime"] = ANCHOR.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        capture_tool(SimpleNamespace(), **kwargs)
