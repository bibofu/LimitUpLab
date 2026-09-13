"""Record two bounded event-list candidates; no model calls or quality approval."""

import json
from datetime import datetime
from pathlib import Path

from app.agent_eval.blueprints import load_blueprints
from app.agent_eval.loader import load_suite, world_digest
from app.agent_eval.local_capture import read_local_session
from app.agent_eval.models import CalendarSpec, CaseSpec, WorldSpec
from app.agent_eval.recorder import capture_tool, digest, save_capture, verify_replay
from app.agents.tools import AgentToolRegistry, V1_AGENT_PROFILE


class LocalEventsRegistry(AgentToolRegistry):
    def __init__(self, events):
        self.events = events
        self.profile = V1_AGENT_PROFILE

    def schemas(self):
        return [item for item in super().schemas() if item.name == "limit_up_events"]

    def is_enabled(self, name):
        return name == "limit_up_events"


def prepare_event_candidates(database: Path, anchor: datetime, book_path: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(destination)
    book = load_blueprints(book_path)
    selected = [item for item in book.cases if item.id in {"OFF-027", "OFF-038"}]
    if len(selected) != 2:
        raise ValueError("both event blueprints are required")
    day, rows, events = read_local_session(database, anchor)
    calendar = CalendarSpec(id="observed-single-session", version=1, start_date=day,
                            end_date=day, trading_dates=[day])
    assets = []
    for blueprint in selected:
        arguments = blueprint.arguments.get("limit_up_events", {})
        if (blueprint.bindings or blueprint.profile != V1_AGENT_PROFILE
                or blueprint.mode != "offline" or blueprint.focus_tool != "limit_up_events"
                or arguments.get("trade_date") != day.isoformat()
                or arguments.get("recent_trade_days", 1) != 1):
            raise ValueError("event slice requires unchanged single-session local blueprints")
        artifact = capture_tool(
            LocalEventsRegistry(events), tool="limit_up_events", arguments=arguments,
            anchor_datetime=anchor, recording_id=blueprint.id + "-events",
            provenance="production limit_up_events over read-only local market events",
            source_manifest={"source": "local-sqlite-limit-up-events", "trade_date": day.isoformat(),
                             "row_count": len(rows), "selected_rows_digest": digest(rows),
                             "scope": "single-session capture; historical revisions not excluded"},
        )
        replay = verify_replay(artifact, calendar=calendar, latest_local_trade_date=day)
        payload = artifact.body.recording.observation.payload
        expected_count = {"OFF-027": 10, "OFF-038": 20}[blueprint.id]
        event_rows = payload.get("events", [])
        if (not replay["passed"] or artifact.body.recording.observation.state != "ok"
                or len(event_rows) != expected_count or payload.get("returned_count") != expected_count
                or payload.get("trade_date") != day.isoformat()
                or len({row["symbol"] for row in event_rows}) != expected_count):
            raise ValueError("recording does not satisfy candidate data prerequisites")
        world = WorldSpec(world_id="candidate-" + blueprint.id.lower(), world_version=1,
                          profile=blueprint.profile, anchor_datetime=anchor, latest_local_trade_date=day,
                          trading_calendar=calendar, tool_contract_version=artifact.body.tool_contract_version,
                          evidence_version=artifact.body.evidence_version, recordings=[artifact.body.recording])
        case = CaseSpec.model_validate({
            "case_id": blueprint.id, "case_version": 1, "profile": blueprint.profile,
            "mode": "offline", "severity": "P1", "status": "candidate",
            "capabilities": blueprint.capabilities,
            "world": {"id": world.world_id, "version": 1},
            "conversation": [{"role": "user", "content": blueprint.question}],
            "expected_requirements": [{"id": "ordered-list", "description": "；".join(blueprint.requirements),
                                       "source_turn": 0, "source_text": blueprint.question}],
            # Membership alone is insufficient: retain ordered identity and date together.
            # This assertion remains needs_review until a calibrated list evaluator exists.
            "assertions": [{"id": "ordered-event-facts", "evaluator": "fact",
                            "kind": "answer_matches_observation", "requirement_id": "ordered-list",
                            "target": "answer.ordered_events",
                            "expected": {"trade_date": day.isoformat(),
                                         "ordered_members": [{"symbol": row["symbol"], "name": row["name"]}
                                                             for row in event_rows]}}],
            "expected_terminal": {"allowed_status": ["complete"], "missing_requirement_ids": []},
        })
        review = {"blueprint_id": blueprint.id, "blueprint_digest": digest(blueprint.model_dump(mode="json")),
                  "source_capture_checksum": artifact.checksum, "world_digest": world_digest(world),
                  "case_digest": digest(case.model_dump(mode="json")), "replay": replay,
                  "status": "candidate", "review_status": "unreviewed", "release_eligible": False,
                  "matched_count": payload["matched_count"], "returned_count": len(event_rows),
                  "visible_preview_rows": len(artifact.body.evidence_view["rows"]),
                  "required_reviews": ["privacy including raw capture fields", "historical revisions",
                                       "independent filter and amount-sort oracle", "requirements and terminal policy",
                                       "calibrated ordered-list claim evaluator", "alternative valid tool arguments"],
                  "limitations": ["single recorded argument signature, not all valid routes",
                                  "observed single-session calendar, not an official trading calendar",
                                  "production event view omits amount; do not demand invented amounts",
                                  "no real model execution or automatic answer-fact pass"]}
        assets.append((case, world, artifact, review))
    # Validate all prerequisites before creating the batch. Never overwrite earlier evidence.
    destination.mkdir(parents=True, exist_ok=False)
    for case, world, artifact, review in assets:
        folder = destination / case.case_id
        folder.mkdir()
        save_capture(artifact, folder / "capture.json")
        for filename, data in (("case.json", case.model_dump(mode="json")),
                               ("world.json", world.model_dump(mode="json")), ("review.json", review)):
            with (folder / filename).open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        load_suite([folder / "case.json"], [folder / "world.json"])
    return {"candidate_count": len(assets), "release_eligible": False,
            "cases": [{"id": c.case_id, "matched_count": r["matched_count"],
                       "returned_count": r["returned_count"], "preview_rows": r["visible_preview_rows"],
                       "replay_passed": r["replay"]["passed"]} for c, _, _, r in assets]}
