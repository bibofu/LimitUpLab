"""Shared local Live adapter: execute production tools on a per-run SQLite copy."""

import argparse
from contextlib import closing, contextmanager
from functools import wraps
import json
from pathlib import Path
import sqlite3

from app.agent_eval.basic70_live import NoRemoteCollector, isolated_io, live_requests
from app.agent_eval.core_batch import write_json
from app.agent_eval.historical_live import HistoricalDataDrift
from app.agent_eval.models import CaseSpec, WorldSpec
from app.agent_eval.recorder import digest, load_capture
from app.agents.query_contract import query_reference_date_override
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository


LOCAL_TOOLS = frozenset(r[0] for r in live_requests("2026-09-11", "000636"))


def baseline_payload(tool, payload):
    # These three production services stamp the invocation time at the root.
    # Keep data_as_of, nested timestamps, all values and missingness unchanged.
    if tool in {"post_limit_screen", "post_limit_path", "post_limit_statistics"} and isinstance(payload, dict):
        return {key: value for key, value in payload.items() if key != "generated_at"}
    return payload


class SnapshotLiveRegistry(AgentToolRegistry):
    def __init__(self, database, baseline, destination):
        self.snapshot = destination.resolve()
        if self.snapshot.exists():
            raise FileExistsError(self.snapshot)
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
             closing(sqlite3.connect(self.snapshot)) as target:
            source.backup(target)
        self.reference_date = baseline.anchor_datetime.date()
        with isolated_io(self.snapshot):
            with closing(sqlite3.connect(self.snapshot)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("SELECT * FROM limit_up_events WHERE trade_date <= ? ORDER BY trade_date, symbol",
                                          (self.reference_date.isoformat(),)).fetchall()
            repository = SQLiteLimitUpRepository(self.snapshot, seed_if_empty=False)
            events = [repository._event_from_row(row) for row in rows]
        super().__init__(events, SQLiteFirstBoardRepository(self.snapshot), NoRemoteCollector(), profile=baseline.profile)
        self.attempts, self.baseline_checks = [], []
        gateway = ToolGateway(self, EvidenceStore())
        with self.anchored():
            for recording in baseline.recordings:
                args = gateway.validate({"name": recording.tool, "args": recording.arguments})
                _, payload, state = gateway.execute(recording.tool, args)
                if (digest(baseline_payload(recording.tool, payload)) !=
                        digest(baseline_payload(recording.tool, recording.observation.payload))
                        or state != recording.observation.state):
                    raise HistoricalDataDrift(f"local snapshot baseline drift: {recording.tool}")
                self.baseline_checks.append(recording.id)

    @property
    def enabled_tool_names(self):
        return LOCAL_TOOLS

    def __getattribute__(self, name):
        method = super().__getattribute__(name)
        # Gateway adapters also call identity resolution before the business method.
        if name in LOCAL_TOOLS | {"resolve_stock_identity", "resolve_stock_symbol"} and callable(method):
            @wraps(method)
            def guarded(*args, **kwargs):
                with isolated_io(self.snapshot):
                    return method(*args, **kwargs)
            return guarded
        return method

    @contextmanager
    def anchored(self):
        with query_reference_date_override(self.reference_date):
            yield self


def build_live_suite(readiness: Path, destination: Path, base_suite: Path | None = None):
    report = json.loads((readiness / "report.json").read_text(encoding="utf-8"))
    if len(report["cases"]) != 10 or any(e["readiness"] != "captured" for e in report["cases"]):
        raise ValueError("ten successful readiness captures required")
    database = (readiness / "source-snapshot.sqlite").resolve(strict=True)
    assets = []
    for entry in report["cases"]:
        capture = load_capture(readiness / entry["capture"])
        if capture.checksum != entry["checksum"] or capture.body.recording.tool != entry["tool"]:
            raise ValueError("readiness capture mismatch")
        body = capture.body
        day = body.anchor_datetime.date()
        world = WorldSpec(world_id=entry["id"] + "-baseline", world_version=1, profile=body.profile,
            anchor_datetime=body.anchor_datetime, latest_local_trade_date=day,
            trading_calendar={"id": "capture-session", "version": 1, "start_date": day, "end_date": day, "trading_dates": [day]},
            tool_contract_version=body.tool_contract_version, evidence_version=body.evidence_version,
            recordings=[body.recording])
        question = entry["question"]
        # Outcome/coverage is assessed against actual observations, not a fixed call route.
        case = CaseSpec(case_id=entry["id"], case_version=1, profile=body.profile,
            mode="live_historical", severity="P1", status="candidate",
            capabilities=["local_snapshot_live", entry["tool"], "evidence_grounding"],
            conversation=[{"role": "user", "content": question}],
            expected_requirements=[{"id": "delivery", "description": question, "source_turn": 0, "source_text": question}],
            assertions=[{"id": "answer-contract", "evaluator": "fact", "kind": "fact_supported",
                "requirement_id": "delivery", "target": "answer.tool_contract", "expected": {
                    "rubric": question, "scope": "Use current-run evidence; distinguish missing, empty and service failure. No trading instructions.",
                    "baseline_tool": entry["tool"], "baseline_observation_digest": digest(body.recording.observation.model_dump(mode="json"))}}],
            expected_terminal={"allowed_status": ["empty", "complete"] if body.recording.observation.state == "empty" else ["complete", "partial"]})
        assets.append((case, world))
    carried = []
    if base_suite:
        base = json.loads(base_suite.read_text(encoding="utf-8"))
        for entry in base["cases"] + base.get("carried_cases", []):
            entry = dict(entry)
            for key in ("case", "world", "baseline"):
                if entry.get(key):
                    path = (base_suite.parent / entry[key]).resolve(strict=True)
                    model = CaseSpec if key == "case" else WorldSpec
                    value = model.model_validate_json(path.read_text(encoding="utf-8"))
                    if digest(value.model_dump(mode="json")) != entry[key + "_digest"]:
                        raise ValueError("base asset digest mismatch")
                    entry[key] = str(path)
            carried.append(entry)
    ids = [c.case_id for c, _ in assets] + [e["id"] for e in carried]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    for case, world in assets:
        folder = destination / case.case_id
        folder.mkdir()
        write_json(folder / "case.json", case.model_dump(mode="json"))
        write_json(folder / "baseline.json", world.model_dump(mode="json"))
        entries.append({"id": case.case_id, "mode": case.mode, "status": case.status,
            "case": case.case_id + "/case.json", "baseline": case.case_id + "/baseline.json",
            "case_digest": digest(case.model_dump(mode="json")), "baseline_digest": digest(world.model_dump(mode="json")),
            "live_database": str(database), "tools": [world.recordings[0].tool]})
    entries += carried
    suite = {"schema_version": "basic70-runnable-candidates-v1", "cases": entries,
        "offline": sum(e["mode"] == "offline" for e in entries),
        "live": sum(e["mode"] != "offline" for e in entries), "new_active_golden": 0,
        "release_eligible": False, "model_calls": 0,
        "limitations": ["Runnable candidates, not approved Golden or measured Agent accuracy.",
                        "Snapshot contains later revisions; policy status is capture-time, not historical point-in-time."]}
    write_json(destination / "suite.json", suite)
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-suite", type=Path)
    args = parser.parse_args()
    report = build_live_suite(args.readiness, args.output_dir, args.base_suite)
    print(json.dumps({key: report[key] for key in ("offline", "live", "new_active_golden")}, ensure_ascii=False))
