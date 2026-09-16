"""Materialize an explicitly user-approved subset without overwriting candidate assets."""

import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest


def promote_approved(source: Path, destination: Path, approved_ids: list[str], *, approval_text: str):
    if destination.exists():
        raise FileExistsError(destination)
    if not approved_ids or len(set(approved_ids)) != len(approved_ids) or not approval_text.strip():
        raise ValueError("explicit unique approved IDs and user approval are required")
    source = source.resolve()
    suite = json.loads(source.read_text(encoding="utf-8"))
    entries = suite["cases"]
    if len({e["id"] for e in entries}) != len(entries):
        raise ValueError("duplicate source case IDs")
    by_id = {e["id"]: e for e in entries}
    if any(i not in by_id or by_id[i]["status"] != "candidate" for i in approved_ids):
        raise ValueError("approval must select existing candidates only")
    selected, pending, approvals = [], [], []
    # Validate every source binding before creating any destination files.
    for entry in entries:
        case = load_case(source.parent / entry["case"])
        field = "world" if "world" in entry else "baseline"
        world = load_world(source.parent / entry[field])
        case_digest = digest(case.model_dump(mode="json"))
        world_digest = digest(world.model_dump(mode="json"))
        expected_world = entry.get(field + "_digest", entry.get("baseline_digest"))
        if (case.case_id != entry["id"] or case.status != entry["status"] or case.mode != entry["mode"]
                or case_digest != entry["case_digest"] or world_digest != expected_world):
            raise ValueError("stale source binding: " + entry["id"])
        if case.status != "active" and case.case_id not in approved_ids:
            pending.append({**entry, "case": str(source.parent / entry["case"]),
                            field: str(source.parent / entry[field])})
            continue
        if case.case_id in approved_ids:
            approvals.append({"case_id": case.case_id, "case_version": case.case_version,
                              "candidate_case_digest": case_digest, "baseline_digest": world_digest})
            case = case.model_copy(update={"status": "active"})
        selected.append((entry, case, world, world_digest))
    destination.mkdir(parents=True)
    (destination / "baselines").mkdir()
    result = []
    for original, case, world, checksum in selected:
        case_path = f"{case.case_id}/case.json"
        baseline_path = f"baselines/{checksum.removeprefix('sha256:')}.json"
        (destination / case.case_id).mkdir()
        write_json(destination / case_path, case.model_dump(mode="json"))
        if not (destination / baseline_path).exists():
            write_json(destination / baseline_path, world.model_dump(mode="json"))
        entry = {**original, "status": "active", "case": case_path,
                 "baseline": baseline_path, "case_digest": digest(case.model_dump(mode="json")),
                 "baseline_digest": checksum, "version": case.case_version}
        entry.pop("world", None)
        entry.pop("world_digest", None)
        result.append(entry)
    approval = {"schema_version": "explicit-subset-approval-v1", "reviewer": "user_in_current_conversation",
                "approval_text": approval_text, "source_suite_digest": digest(suite),
                "approved_items": ["question_and_business_scope", "expected_business_facts", "scoring_rules"],
                "cases": approvals, "answer_quality_approved": False}
    report = {"schema_version": "active-golden-suite-v1", "suite_id": destination.name,
              "status": "active", "case_count": len(result), "new_active_golden": len(approvals),
              "offline": sum(e["mode"] == "offline" for e in result),
              "historical_live": sum(e["mode"] == "live_historical" for e in result),
              "cases": result, "approval_digest": digest(approval), "release_eligible": False,
              "answer_quality_approved": False,
              "limitations": ["User-approved evaluation contracts; not a claim that the Agent passes.",
                              "Combined Judge remains diagnostic; manual review is required for disputed claims.",
                              "Live database references retain their original snapshot dependency."]}
    write_json(destination / "approval.json", approval)
    write_json(destination / "pending.json", {"cases": pending, "case_count": len(pending)})
    write_json(destination / "suite.json", report)
    return report
