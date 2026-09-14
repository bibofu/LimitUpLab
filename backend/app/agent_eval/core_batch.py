"""Five concrete offline cases and one bounded historical-live candidate."""

from datetime import datetime, date
import json
from pathlib import Path

from app.agent_eval.blueprints import load_blueprints
from app.agents.tools import AgentToolRegistry
from app.agent_eval.local_capture import LocalSummaryRegistry, read_local_session
from app.agent_eval.models import CalendarSpec, CaseSpec, WorldSpec
from app.agent_eval.recorder import capture_tool, digest, save_capture, verify_replay


class LocalResearchRegistry(LocalSummaryRegistry):
    def schemas(self):
        return [s for s in AgentToolRegistry.schemas(self)
                if s.name in {"market_summary", "limit_up_events"}]

    def is_enabled(self, name):
        return name in {"market_summary", "limit_up_events"}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def prepare_core_batch(database: Path, book_path: Path, destination: Path):
    if destination.exists():
        raise FileExistsError(destination)
    book = {c.id: c for c in load_blueprints(book_path).cases}
    anchor = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
    events, source_rows = [], []
    for day in (8, 10, 11):
        _, rows, items = read_local_session(database, anchor.replace(day=day))
        events.extend(items)
        source_rows.extend(rows)
    day8 = [e for e in events if e.trade_date.day == 8]
    closed8 = [e for e in day8 if e.closed_limit]
    height = max(e.board_height for e in closed8)
    highest = [e for e in closed8 if e.board_height == height]
    broken = [e for e in day8 if e.symbol.startswith("688") and e.break_count > 0]
    if len(highest) < 2 or not any(e.closed_limit for e in broken) or not any(not e.closed_limit for e in broken):
        raise ValueError("September 8 no longer satisfies ties/reclosure prerequisites")
    registry = LocalResearchRegistry(events)
    manifest = {"source": "readonly-local-events", "selected_rows_digest": digest(source_rows),
                "dates": ["2026-09-08", "2026-09-10", "2026-09-11"],
                "scope": "observed sessions only; later revisions and outcome placeholders possible"}
    captures, signatures = [], set()
    # Record actual parameter routes, including larger retrieval and equivalent explicit statuses.
    bases = [{"trade_date": "2026-09-08", "highest_only": True},
             {"trade_date": "2026-09-08"}, {"trade_date": "2026-09-10"},
             {"trade_date": "2026-09-11"},
             {"trade_date": "2026-09-08", "market": "star_market", "event_status": "broken_intraday"},
             {"trade_date": "2026-09-08", "market": "star_market", "broken_only": True},
             {"trade_date": "2026-09-11", "query": "评测不存在主题"}]
    for base in bases:
        for limit in (30, 100):
            for closed in (None, True):
                args = {**base, "limit": limit}
                if closed is not None:
                    args["closed_only"] = closed
                signature = digest(args)
                if signature in signatures:
                    continue
                signatures.add(signature)
                captures.append(capture_tool(registry, tool="limit_up_events", arguments=args,
                    anchor_datetime=anchor, recording_id="events-" + str(len(captures)),
                    provenance="production local events executed, not hand-authored returns", source_manifest=manifest))
    captures.append(capture_tool(registry, tool="market_summary", arguments={"include_limit_down": False},
        anchor_datetime=anchor, recording_id="summary", provenance="production local summary", source_manifest=manifest))
    calendar = CalendarSpec(id="observed-three-sessions", version=1, start_date=date(2026,9,8),
        end_date=date(2026,9,13), trading_dates=[date(2026,9,d) for d in (8,10,11)])
    for capture in captures:
        if not verify_replay(capture, calendar=calendar, latest_local_trade_date=date(2026,9,11))["passed"]:
            raise ValueError("capture replay differs")
    closed10 = [e for e in events if e.trade_date.day == 10 and e.closed_limit]
    closed11 = [e for e in events if e.trade_date.day == 11 and e.closed_limit]
    if len(closed10) == len(closed11):
        raise ValueError("historical and latest counts must differ")
    empty_capture = next(c for c in captures if c.body.recording.arguments.get("query"))
    if empty_capture.body.recording.observation.payload["matched_count"] != 0:
        raise ValueError("empty query is not empty")
    facts = {
        "OFF-010": {"trade_date": "2026-09-08", "max_board_height": height,
                    "members": [{"symbol":e.symbol,"name":e.name} for e in highest]},
        "OFF-028": {"trade_date": "2026-09-08", "members": [
            {"symbol":e.symbol,"name":e.name,"closed_limit":e.closed_limit} for e in broken]},
        "OFF-031": {"trade_date": "2026-09-10", "limit_up_count": len(closed10)},
        "OFF-032": {"trade_date": "2026-09-11", "limit_up_count": len(closed11)},
        "OFF-033": {"trade_date": "2026-09-11", "matched_count": 0, "members": []},
    }
    assets = []
    for key, expected in facts.items():
        blueprint = book[key]
        question = blueprint.question.replace("2026-09-11", "2026-09-08") if key in {"OFF-010","OFF-028"} else blueprint.question
        world = WorldSpec(world_id="candidate-" + key.lower(), world_version=1, profile=blueprint.profile,
            anchor_datetime=anchor.replace(day=13) if key == "OFF-032" else anchor,
            latest_local_trade_date=date(2026,9,11), trading_calendar=calendar,
            tool_contract_version=captures[0].body.tool_contract_version,
            evidence_version=captures[0].body.evidence_version,
            recordings=[c.body.recording for c in captures])
        assertions = [{"id":"facts", "evaluator":"fact", "kind":"fact_supported",
            "target":"answer" if key == "OFF-032" else "answer.business_contract",
            "requirement_id":"delivery", "expected":expected}]
        if key == "OFF-032":
            assertions.append({"id":"local-only", "evaluator":"trajectory", "kind":"argument_equals",
                "target":"market_summary.include_limit_down", "requirement_id":"delivery", "expected":False})
        case = CaseSpec.model_validate({"case_id":key,"case_version":1,"profile":blueprint.profile,
            "mode":"offline","severity":"P1","status":"candidate","capabilities":blueprint.capabilities,
            "world":{"id":world.world_id,"version":1},"conversation":[{"role":"user","content":question}],
            "expected_requirements":[{"id":"delivery","description":"；".join(blueprint.requirements),
                                      "source_turn":0,"source_text":question}], "assertions":assertions,
            "expected_terminal":{"allowed_status":["complete","empty"] if key == "OFF-033" else ["complete"],
                                 "missing_requirement_ids":[]}})
        assets.append((case, world))
    destination.mkdir(parents=True)
    recordings = destination / "recordings"
    recordings.mkdir()
    for c in captures:
        save_capture(c, recordings / (c.body.recording.id + ".json"))
    for case, world in assets:
        folder = destination / case.case_id
        folder.mkdir()
        write_json(folder / "case.json", case.model_dump(mode="json"))
        write_json(folder / "world.json", world.model_dump(mode="json"))
        write_json(folder / "review.json", {"status":"unreviewed","release_eligible":False,
            "source":manifest,"case_digest":digest(case.model_dump(mode="json")),
            "world_digest":digest(world.model_dump(mode="json")),
            "date_adjustment":"OFF-010/OFF-028 rebound to verified September 8 with user approval",
            "terminal_note":"OFF-033 yes/no query permits complete or empty pending semantic review",
            "limitations":["calendar lists only loaded sessions; not valid for N-trading-day arithmetic",
                            "unsupported business fact assertions remain needs_review",
                            "outcome placeholders are not mature future observations"]})
    # Historical Live has no frozen-world reference. World-shaped file is baseline evidence only.
    blueprint = book["LH-004"]
    live = assets[0][0].model_copy(deep=True)
    live.case_id, live.mode, live.world = "LH-004", "live_historical", None
    live.capabilities = blueprint.capabilities
    live.conversation[0].content = blueprint.question
    live.expected_requirements[0].source_text = blueprint.question
    live.expected_requirements[0].description = "最高高度与全部并列成员"
    max11 = max(e.board_height for e in closed11)
    live.assertions[0].expected = {"trade_date":"2026-09-11","max_board_height":max11,
        "members":[{"symbol":e.symbol,"name":e.name} for e in closed11 if e.board_height==max11]}
    live = CaseSpec.model_validate_json(live.model_dump_json())
    folder = destination / live.case_id
    folder.mkdir()
    write_json(folder / "case.json", live.model_dump(mode="json"))
    write_json(folder / "baseline.json", assets[0][1].model_dump(mode="json"))
    write_json(folder / "review.json", {"release_eligible":False,"status":"unreviewed",
        "scope":"bounded local historical tool run; not full-profile Live coverage",
        "baseline_source":manifest,"requires_real_tool_execution":True})
    return {"offline_candidates":5,"historical_live_candidates":1,"recordings":len(captures),
            "release_eligible":False,"expected_facts":facts}
