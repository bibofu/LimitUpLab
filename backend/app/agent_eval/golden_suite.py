"""Assemble independently accepted Active assets into one self-contained Golden suite."""

import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest


def _active_entries(source: Path):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "active":
        raise ValueError("source manifest is not active")
    if "cases" in manifest:
        return manifest, [(item, source / item["case_id"]) for item in manifest["cases"]]
    item = {key: manifest[key] for key in ("case_id", "case_version", "mode", "active_case_digest", "baseline_digest")}
    return manifest, [(item, source)]


def assemble_golden_suite(sources: list[Path], expected_bundles: list[Path], destination: Path,
                          *, required_counts=(20, 10)):
    if destination.exists():
        raise FileExistsError(destination)
    expected = {}
    for bundle in expected_bundles:
        suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
        for item in suite["cases"]:
            expected[item["id"]] = item["mode"]
    collected, source_records = {}, []
    for source in sources:
        manifest, entries = _active_entries(source)
        source_records.append({"manifest_digest": digest(manifest), "schema_version": manifest.get("schema_version"),
                               "scope": manifest.get("scope")})
        for item, folder in entries:
            case_id = item["case_id"]
            if case_id in collected:
                raise ValueError("duplicate active case ID")
            case = load_case(folder / "case.json")
            baseline_name = "world.json" if case.mode == "offline" else "baseline.json"
            world = load_world(folder / baseline_name)
            if (case.status != "active" or case.case_id != case_id or case.case_version != item["case_version"]
                    or case.mode != item["mode"] or digest(case.model_dump(mode="json")) != item["active_case_digest"]
                    or digest(world.model_dump(mode="json")) != item["baseline_digest"]):
                raise ValueError("active case or baseline binding is stale")
            collected[case_id] = (case, world, baseline_name, item)
    if set(collected) != set(expected):
        missing, extra = sorted(set(expected) - set(collected)), sorted(set(collected) - set(expected))
        raise ValueError(f"active suite coverage mismatch; missing={missing}, extra={extra}")
    if any(collected[key][0].mode != mode for key, mode in expected.items()):
        raise ValueError("active suite mode differs from expected bundle")
    destination.mkdir(parents=True)
    entries, baselines = [], {}
    for case_id in sorted(collected):
        case, world, baseline_name, source_item = collected[case_id]
        folder = destination / case_id
        folder.mkdir()
        write_json(folder / "case.json", case.model_dump(mode="json"))
        baseline_digest = digest(world.model_dump(mode="json"))
        shared_baseline = f"baselines/{baseline_digest.removeprefix('sha256:')}.json"
        baselines.setdefault(shared_baseline, world)
        entries.append({"id": case_id, "version": case.case_version, "mode": case.mode,
                        "case": f"{case_id}/case.json", "baseline": shared_baseline,
                        "case_digest": digest(case.model_dump(mode="json")),
                        "baseline_digest": baseline_digest,
                        "scope": source_item.get("scope")})
    (destination / "baselines").mkdir()
    for path, world in baselines.items():
        write_json(destination / path, world.model_dump(mode="json"))
    report = {"schema_version": "active-golden-suite-v1", "suite_id": "local30-current",
              "status": "active", "offline": sum(item["mode"] == "offline" for item in entries),
              "historical_live": sum(item["mode"] == "live_historical" for item in entries),
              "case_count": len(entries), "cases": entries, "sources": source_records,
              "release_eligible": False, "answer_quality_approved": False,
              "limitations": ["Active means the case contract and evaluator are qualified, not that the current Agent passes",
                              "full-answer additional claims remain outside scoped core contracts"]}
    required_offline, required_live = required_counts
    if (report["offline"], report["historical_live"], report["case_count"]) != (
            required_offline, required_live, required_offline + required_live):
        raise ValueError("Golden suite has an unexpected Offline/Historical Live split")
    write_json(destination / "suite.json", report)
    return report
