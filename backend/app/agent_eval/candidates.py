"""Prepare unreviewed local candidates from verified recordings, never activate them."""

from datetime import date
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent_eval.loader import load_suite, world_digest
from app.agent_eval.models import CalendarSpec, CaseSpec, WorldSpec
from app.agent_eval.recorder import CaptureArtifact, digest, verify_replay


def summary_candidate(artifact: CaptureArtifact) -> tuple[CaseSpec, WorldSpec]:
    artifact = CaptureArtifact.model_validate_json(artifact.model_dump_json())
    body = artifact.body
    record = body.recording
    if record.tool != "market_summary" or record.origin != "recorded":
        raise ValueError("candidate requires recorded market_summary")
    payload = record.observation.payload
    if not isinstance(payload, dict) or record.observation.state not in {"ok", "partial"}:
        raise ValueError("candidate requires usable summary observation")
    day = date.fromisoformat(payload["trade_date"])
    if day != body.anchor_datetime.astimezone(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("single-session candidate requires anchor to match observed date")
    count = payload.get("limit_up_count")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    if record.arguments.get("include_limit_down", False) is not False:
        raise ValueError("candidate forbids remote limit-down collection")
    if record.tool_result_input is None or record.tool_result_input.get("include_limit_down") is not False:
        raise ValueError("recorded tool input must confirm local-only scope")
    calendar = CalendarSpec(id="observed-single-session", version=1, start_date=day,
                            end_date=day, trading_dates=[day])
    if not verify_replay(artifact, calendar=calendar, latest_local_trade_date=day)["passed"]:
        raise ValueError("capture does not reproduce its visible evidence")
    world = WorldSpec(world_id="candidate-summary-" + day.isoformat(), world_version=1,
        profile=body.profile, anchor_datetime=body.anchor_datetime, latest_local_trade_date=day,
        trading_calendar=calendar, tool_contract_version=body.tool_contract_version,
        evidence_version=body.evidence_version, recordings=[record.model_copy(deep=True)])
    question = "查询最新本地交易日的涨停家数，并标明数据日期；不查跌停数据。"
    case = CaseSpec.model_validate({
        "case_id": "core-local-summary-count", "case_version": 1, "profile": body.profile,
        "mode": "offline", "severity": "P1", "status": "candidate",
        "capabilities": ["market_summary", "latest_local", "count", "scope_control"],
        "world": {"id": world.world_id, "version": world.world_version},
        "conversation": [{"role": "user", "content": question}],
        "expected_requirements": [{"id": "summary", "description": "交付最新本地日期及涨停家数，不查询跌停",
                                   "source_turn": 0, "source_text": question}],
        "assertions": [
            {"id": "summary-tool", "evaluator": "trajectory", "kind": "tool_required",
             "requirement_id": "summary", "target": "market_summary"},
            {"id": "local-only", "evaluator": "trajectory", "kind": "argument_equals",
             "requirement_id": "summary", "target": "market_summary.include_limit_down", "expected": False},
            {"id": "summary-facts", "evaluator": "fact", "kind": "fact_supported",
             "requirement_id": "summary", "target": "answer",
             "expected": {"trade_date": day.isoformat(), "limit_up_count": count}},
        ],
        "expected_terminal": {"allowed_status": ["complete"], "missing_requirement_ids": []},
    })
    return case, world


def save_summary_candidate(artifact: CaptureArtifact, destination: Path) -> dict:
    case, world = summary_candidate(artifact)
    manifest = {"status": "candidate", "review_status": "unreviewed", "release_eligible": False,
                "source_capture_checksum": artifact.checksum,
                "case_digest": digest(case.model_dump(mode="json")), "world_digest": world_digest(world),
                "calendar_scope": "observed single session only",
                "required_reviews": ["privacy", "source freshness and historical revisions",
                                     "user requirements and expected facts", "terminal policy"]}
    destination.mkdir(parents=True, exist_ok=False)
    for filename, model in [("case.json", case), ("world.json", world)]:
        with (destination / filename).open("x", encoding="utf-8") as handle:
            handle.write(model.model_dump_json(indent=2) + "\n")
    # Metadata is an asset provenance record, never a quality report or an approval.
    with (destination / "review.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    load_suite([destination / "case.json"], [destination / "world.json"])
    return manifest
