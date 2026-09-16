"""Assemble independently accepted Active assets into one self-contained Golden suite."""

import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.models import WorldSpec
from app.agent_eval.recorder import digest


def _active_entries(source: Path):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "active":
        raise ValueError("source manifest is not active")
    if "cases" in manifest:
        return manifest, [(item, source / item["case_id"]) for item in manifest["cases"]]
    item = {key: manifest[key] for key in ("case_id", "case_version", "mode", "active_case_digest", "baseline_digest")}
    return manifest, [(item, source)]


def _recording_index(world, registry):
    return {(item.tool, digest(registry._arguments(item.tool, item.arguments))): item
            for item in world.recordings}


def _additive_recording_diff(old_world, new_world, old_registry, new_registry):
    old_records = _recording_index(old_world, old_registry)
    new_records = _recording_index(new_world, new_registry)
    missing = set(old_records) - set(new_records)
    changed = [key for key in old_records.keys() & new_records.keys()
               if (old_records[key].observation != new_records[key].observation
                   or old_records[key].tool_result_input != new_records[key].tool_result_input)]
    added = sorted(set(new_records) - set(old_records))
    if missing or changed or not added:
        raise ValueError(f"World is not a strict additive extension; missing={len(missing)}, changed={len(changed)}, added={len(added)}")
    return old_records, added


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


def migrate_additive_world(source: Path, target_bundle: Path, acceptance_path: Path,
                           case_id: str, destination: Path, acceptance_destination: Path,
                           route_specs: list[dict]):
    """Replace one Active case World only after proving the new recordings are additive."""
    if destination.exists() or acceptance_destination.exists():
        raise FileExistsError(destination if destination.exists() else acceptance_destination)
    source_suite = json.loads((source / "suite.json").read_text(encoding="utf-8"))
    target_suite = json.loads((target_bundle / "suite.json").read_text(encoding="utf-8"))
    source_entries = {item["id"]: item for item in source_suite["cases"]}
    if source_suite.get("status") != "active" or case_id not in source_entries or not route_specs:
        raise ValueError("migration requires an Active source case and explicit additive routes")
    source_entry = source_entries[case_id]
    active_case = load_case(source / source_entry["case"])
    candidate_case = active_case.model_copy(update={"status": "candidate"})
    if active_case.status != "active":
        raise ValueError("additive World migration cannot change the reviewed case contract")
    old_world = load_world(source / source_entry["baseline"])
    recording_world = load_world(target_bundle / "world.json")
    old_registry, recording_registry = FrozenAgentToolRegistry(old_world), FrozenAgentToolRegistry(recording_world)
    available = _recording_index(recording_world, recording_registry)
    additions = []
    for position, spec in enumerate(route_specs, 1):
        key = (spec["tool"], digest(recording_registry._arguments(spec["tool"], spec["arguments"])))
        if key not in available:
            raise ValueError(f"requested additive route is absent from recorded target: {position}")
        suffix = digest({"tool": key[0], "arguments_digest": key[1]}).removeprefix("sha256:")[:12]
        additions.append(available[key].model_copy(update={"id": f"additive-{suffix}"}))
    new_world = WorldSpec.model_validate(old_world.model_copy(
        deep=True, update={"recordings": [*old_world.recordings, *additions]},
    ).model_dump(mode="json"))
    new_registry = FrozenAgentToolRegistry(new_world)

    old_records, added = _additive_recording_diff(old_world, new_world, old_registry, new_registry)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    batched = "cases" in acceptance
    accepted = (next((item for item in acceptance.get("cases", []) if item["case_id"] == case_id), None)
                if batched else acceptance if acceptance.get("case_id") == case_id else None)
    old_digest, new_digest = digest(old_world.model_dump(mode="json")), digest(new_world.model_dump(mode="json"))
    if (acceptance.get("technical_acceptance") is not True or not accepted
            or accepted.get("case_digest") != digest(candidate_case.model_dump(mode="json"))
            or accepted.get("baseline_digest") != old_digest):
        raise ValueError("technical acceptance is missing or stale")
    routes = [accepted["route"]] if "route" in accepted else accepted.get("routes", [])
    if not routes:
        raise ValueError("technical acceptance has no replayable route")
    with new_registry.anchored():
        results = new_registry.execute_frozen_calls(
            [{"name": route["tool"], "arguments": route["arguments"]} for route in routes], request=None,
        )["tool_results"]
    if any(result.status != "success" for result in results):
        raise ValueError("previously accepted route no longer succeeds")
    if batched:
        acceptance["cases"] = [{**item, "baseline_digest": new_digest} if item["case_id"] == case_id else item
                               for item in acceptance["cases"]]
    else:
        acceptance["baseline_digest"] = new_digest
    migration = {"schema_version": "additive-world-migration-v1", "case_id": case_id,
                 "source_suite_digest": digest(source_suite), "target_suite_digest": digest(target_suite),
                 "source_baseline_digest": old_digest, "target_baseline_digest": new_digest,
                 "preserved_recordings": len(old_records), "added_recordings": len(added),
                 "added_routes": [{"tool": tool, "arguments_digest": arguments_digest}
                                  for tool, arguments_digest in added],
                 "case_contract_unchanged": True, "accepted_routes_replayed": len(routes)}
    acceptance["additive_world_migration"] = migration
    destination.mkdir(parents=True)
    (destination / "baselines").mkdir()
    baselines, entries = {}, []
    for item in source_suite["cases"]:
        case = load_case(source / item["case"])
        world = new_world if item["id"] == case_id else load_world(source / item["baseline"])
        folder = destination / item["id"]
        folder.mkdir()
        write_json(folder / "case.json", case.model_dump(mode="json"))
        world_digest = digest(world.model_dump(mode="json"))
        baseline = f"baselines/{world_digest.removeprefix('sha256:')}.json"
        baselines.setdefault(baseline, world)
        entries.append({**item, "baseline": baseline, "baseline_digest": world_digest})
    for path, world in baselines.items():
        write_json(destination / path, world.model_dump(mode="json"))
    migrated_suite = {**source_suite, "cases": entries,
                      "sources": [*source_suite.get("sources", []), migration]}
    write_json(destination / "suite.json", migrated_suite)
    acceptance_destination.mkdir(parents=True)
    write_json(acceptance_destination / "acceptance.json", acceptance)
    write_json(destination / "additive-migration.json", migration)
    return migration
