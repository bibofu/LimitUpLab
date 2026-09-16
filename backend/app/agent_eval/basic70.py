"""Build reviewable Offline candidates from explicit synthetic tool contracts."""

import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.models import CaseSpec, WorldSpec
from app.agent_eval.recorder import digest, stable_view
from app.agents.react_runtime.evidence import EvidenceStore, EVIDENCE_VERSION
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_CONTRACT_VERSION, TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES


DEFAULT_RECIPE = Path(__file__).resolve().parents[2] / "evals/suites/basic70-expansion.json"


def check_values(payload, checks):
    """Literal key/index paths only; distinguish missing fields from explicit null."""
    for check in checks:
        value = payload
        for key in check["path"]:
            value = value[key]
        if digest(value) != digest(check["equals"]):
            raise ValueError("fixture contradicts independently declared expected value")


def candidate_assets(item):
    recordings = [{k: item[k] for k in ("tool", "arguments", "payload", "state", "checks")}]
    recordings.extend(item.get("additional_recordings", []))
    names = {r["tool"] for r in recordings}
    known = {t.name for t in TOOL_SCHEMAS}
    if not names <= known:
        raise ValueError("unknown business tool")
    profile = "v1_close_review" if names <= V1_CLOSED_MARKET_TOOL_NAMES else "extended"
    world = WorldSpec.model_validate({
        "world_id": item["id"] + "-synthetic", "world_version": 1, "profile": profile,
        "anchor_datetime": "2026-09-11T18:00:00+08:00", "latest_local_trade_date": "2026-09-11",
        "trading_calendar": {"id": "synthetic-week", "version": 1, "start_date": "2026-09-07",
                             "end_date": "2026-09-11", "trading_dates": [f"2026-09-{day:02}" for day in range(7, 12)]},
        "tool_contract_version": TOOL_CONTRACT_VERSION, "evidence_version": EVIDENCE_VERSION,
        "recordings": [{"id": item["id"] + f"-r{index}", "tool": r["tool"], "arguments": r["arguments"],
                        "origin": "synthetic", "provenance": "authored minimal contract fixture; not real market data",
                        "observation": {"state": r["state"], "payload": r["payload"],
                                        "summary": "合成评测工具记录；依据结构化字段回答。",
                                        "source_errors": r["payload"].get("source_errors", [])
                                        if isinstance(r["payload"], dict) else []}}
                       for index, r in enumerate(recordings)]})
    question = item["question"]
    case = CaseSpec.model_validate({
        "case_id": item["id"], "case_version": 1, "profile": profile, "mode": "offline",
        "severity": "P1", "status": "candidate", "capabilities": ["tool_contract", item["tool"], item["state"]],
        "world": {"id": world.world_id, "version": 1},
        "conversation": [{"role": "user", "content": question}],
        "expected_requirements": [{"id": "delivery", "description": question,
                                   "source_turn": 0, "source_text": question}],
        "assertions": [{"id": "answer-contract", "evaluator": "fact", "kind": "fact_supported",
                        "requirement_id": "delivery", "target": "answer.tool_contract",
                        "expected": {"required_facts": [{"tool": r["tool"], "checks": r["checks"]} for r in recordings],
                                     "reference_answer": item["reference_answer"],
                                     "negative_answer": item["negative_answer"],
                                     "grading": "Meaning and evidence support, not literal answer matching."}}],
        "expected_terminal": {"allowed_status": [item["terminal"]],
                              "missing_requirement_ids": ["delivery"] if item["terminal"] == "partial" else []}})
    return case, world, recordings


def preflight_candidate(item):
    case, world, records = candidate_assets(item)
    registry = FrozenAgentToolRegistry(world)
    gateway = ToolGateway(registry, EvidenceStore())
    views = []
    with registry.anchored():
        for record in records:
            args = gateway.validate({"name": record["tool"], "args": record["arguments"]})
            result, payload, state = gateway.execute(record["tool"], args)
            check_values(payload, record["checks"])
            if digest(payload) != digest(record["payload"]) or state != record["state"]:
                raise ValueError("replay changed fixture")
            store = EvidenceStore()
            key = store.add(tool=record["tool"], payload=payload, state=state, arguments=result.input)
            views.append(stable_view(store.view(key)))
    return case, world, {"fixture_valid": True, "replay_calls": len(records), "evidence_views": views,
                         "model_calls": 0, "answer_quality_verified": False, "release_eligible": False}


def build_basic70(destination: Path, recipe: Path = DEFAULT_RECIPE, base_suite: Path | None = None):
    book = json.loads(recipe.read_text(encoding="utf-8"))
    if book["origin"] != "synthetic" or book["status"] != "candidate":
        raise ValueError("only explicit synthetic candidates may be built")
    ids = [item["id"] for item in book["cases"]]
    if len(ids) != len(set(ids)) or len({i["question"] for i in book["cases"]}) != len(ids):
        raise ValueError("duplicate candidate id or question")
    if any(not key.startswith("OFF-B") or not key.removeprefix("OFF-B").isdigit() for key in ids):
        raise ValueError("invalid candidate id")
    if destination.exists():
        raise FileExistsError(destination)
    assets = [(item, *preflight_candidate(item)) for item in book["cases"]]
    carried = []
    if base_suite:
        base = json.loads(base_suite.read_text(encoding="utf-8"))
        for entry in base["cases"]:
            case_path = (base_suite.parent / entry["case"]).resolve()
            case = CaseSpec.model_validate_json(case_path.read_text(encoding="utf-8"))
            if digest(case.model_dump(mode="json")) != entry["case_digest"]:
                raise ValueError("base case digest mismatch")
            world_key = "world" if entry.get("world") else "baseline"
            world_path = (base_suite.parent / entry[world_key]).resolve()
            world = WorldSpec.model_validate_json(world_path.read_text(encoding="utf-8"))
            if digest(world.model_dump(mode="json")) != entry[world_key + "_digest"]:
                raise ValueError("base world digest mismatch")
            carried.append({"id": case.case_id, "mode": case.mode, "status": case.status,
                            "case": str(case_path), "case_digest": entry["case_digest"],
                            world_key: str(world_path), world_key + "_digest": entry[world_key + "_digest"],
                            "tools": sorted({r.tool for r in world.recordings}),
                            "asset_origin": "existing_suite_reference"})
        if set(ids) & {entry["id"] for entry in carried}:
            raise ValueError("candidate collides with existing case")
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    for item, case, world, preflight in assets:
        case_dir = destination / case.case_id
        case_dir.mkdir()
        write_json(case_dir / "case.json", case.model_dump(mode="json"))
        write_json(case_dir / "world.json", world.model_dump(mode="json"))
        write_json(case_dir / "preflight.json", preflight)
        entries.append({"id": case.case_id, "mode": "offline", "status": "candidate",
                        "case": case.case_id + "/case.json", "world": case.case_id + "/world.json",
                        "tools": sorted({r.tool for r in world.recordings}),
                        "case_digest": digest(case.model_dump(mode="json")),
                        "world_digest": digest(world.model_dump(mode="json"))})
    report = {"schema_version": "basic70-candidate-bundle-v1", "recipe_digest": digest(book),
              "new_offline_candidates": len(entries), "new_candidate_tools": sorted({t for e in entries for t in e["tools"]}),
              "existing_offline": sum(e["mode"] == "offline" for e in carried),
              "existing_live": sum(e["mode"] != "offline" for e in carried),
              "offline_assets_including_candidates": len(entries) + sum(e["mode"] == "offline" for e in carried),
              "asset_tool_union": sorted({t for e in entries + carried for t in e["tools"]}),
              "new_active_golden": 0, "new_live": 0, "model_calls": 0,
              "release_eligible": False, "cases": entries, "carried_cases": carried,
              "limitations": ["Synthetic minimal contracts, not production output-schema certification.",
                              "Replay validity is not Agent answer correctness or Golden approval.",
                              "Unrecorded arguments fail closed; broaden routes only after review."]}
    write_json(destination / "suite.json", report)
    return report
