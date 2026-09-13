"""Read JSON evaluation assets without executing tools, models or asset expressions."""

import hashlib
import json
from pathlib import Path

from app.agent_eval.models import CaseSpec, WorldSpec


def load_case(path: Path) -> CaseSpec:
    return CaseSpec.model_validate_json(path.read_text(encoding="utf-8"))


def load_world(path: Path) -> WorldSpec:
    return WorldSpec.model_validate_json(path.read_text(encoding="utf-8"))


def world_digest(world: WorldSpec) -> str:
    payload = json.dumps(
        world.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_suite(case_paths: list[Path], world_paths: list[Path]) -> tuple[list[CaseSpec], list[WorldSpec]]:
    cases = [load_case(path) for path in case_paths]
    worlds = [load_world(path) for path in world_paths]
    index = {(world.world_id, world.world_version): world for world in worlds}
    if len(index) != len(worlds):
        raise ValueError("duplicate world ID/version")
    if len({(case.case_id, case.case_version) for case in cases}) != len(cases):
        raise ValueError("duplicate case ID/version")
    for case in cases:
        if case.world is None:
            continue
        world = index.get((case.world.id, case.world.version))
        if world is None:
            raise ValueError(f"missing world for {case.case_id}")
        if world.profile != case.profile:
            raise ValueError(f"profile mismatch for {case.case_id}")
    return cases, worlds
