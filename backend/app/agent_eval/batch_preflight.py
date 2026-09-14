"""No-LLM preflight for a user-reviewed evaluation batch."""

import json
from pathlib import Path

from app.agent_eval.approval import APPROVED_ITEMS
from app.agent_eval.business_facts import _source_rows, _truth, identity
from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest


def approval_index(records):
    """Index only affirmative case entries; rejected cases can never shadow approval."""
    index = {}
    for record in records:
        if (record.get("reviewer") != "user_in_current_conversation"
                or not APPROVED_ITEMS <= set(record.get("approved_items", []))):
            raise ValueError("batch business approval is incomplete")
        for item in record.get("cases", []):
            case_id = item.get("case_id")
            if not case_id or case_id in index:
                raise ValueError("missing or duplicate approved case")
            index[case_id] = {"binding": item, "approval_digest": digest(record)}
    return index


def _baseline(bundle, entry):
    key = entry.get("world") or entry.get("baseline")
    if not key:
        raise ValueError("case entry lacks World/Live baseline")
    return load_world(bundle / key)


def _oracle(case, world):
    facts = [assertion for assertion in case.assertions if assertion.evaluator == "fact"]
    if not facts:
        return {"category": "semantic_terminal", "passed": True, "assertions": 0}
    if len(facts) != 1 or facts[0].target not in {"answer", "answer.business_contract"}:
        raise ValueError("unsupported fact assertion layout")
    expected = facts[0].expected
    if not isinstance(expected, dict):
        raise ValueError("fact assertion lacks structured expected facts")
    scalars, rows = _truth(expected, world, _source_rows(world))
    if any(expected.get(key) != value for key, value in scalars.items()):
        raise ValueError("expected scalar contradicts baseline")
    if "members" in expected:
        actual = [identity(row.get("symbol"), row.get("name")) for row in rows]
        declared = [identity(row.get("symbol"), row.get("name")) for row in expected["members"]]
        if (actual != declared if expected.get("ordered") else set(actual) != set(declared)):
            raise ValueError("expected members contradict baseline")
        if len(declared) != len(set(declared)):
            raise ValueError("expected members contain duplicates")
    keys = set(expected)
    supported = (keys <= {"trade_date", "limit_up_count"}
                 or keys <= {"trade_date", "max_board_height", "members"}
                 or keys <= {"trade_date", "row_selection", "ordered", "members"})
    if not supported:
        raise ValueError("fact contract has no generic calibration adapter")
    category = "count" if "limit_up_count" in expected else "highest" if "max_board_height" in expected else "selection"
    return {"category": category, "passed": True, "assertions": 1,
            "member_count": len(expected.get("members", [])), "ordered": bool(expected.get("ordered"))}


def preflight_batch(bundle: Path, approval_paths: list[Path], destination: Path, *, case_ids=None):
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    entries = {item["id"]: item for item in suite["cases"]}
    selected = list(case_ids) if case_ids else list(entries)
    unknown = set(selected) - set(entries)
    if unknown:
        raise ValueError("unknown cases: " + ", ".join(sorted(unknown)))
    results = []
    for case_id in selected:
        entry = entries[case_id]
        approval = approvals.get(case_id)
        if approval is None:
            results.append({"case_id": case_id, "verdict": "blocked", "reason": "business approval absent"})
            continue
        case, world = load_case(bundle / entry["case"]), _baseline(bundle, entry)
        binding = approval["binding"]
        if (binding.get("case_version") != case.case_version
                or binding.get("case_digest") != digest(case.model_dump(mode="json"))
                or binding.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or entry.get("oracle_crosscheck") is not True):
            results.append({"case_id": case_id, "verdict": "blocked", "reason": "approval or oracle binding is stale"})
            continue
        try:
            oracle = _oracle(case, world)
            verdict, reason = "pass", None
        except (ValueError, TypeError, KeyError, StopIteration) as error:
            oracle, verdict, reason = None, "blocked", str(error)
        results.append({"case_id": case_id, "mode": case.mode, "verdict": verdict,
                        "reason": reason, "approval_digest": approval["approval_digest"], "oracle": oracle})
    counts = {}
    for item in results:
        key = item["oracle"]["category"] if item["verdict"] == "pass" else "blocked"
        counts[key] = counts.get(key, 0) + 1
    report = {"schema_version": "agent-eval-batch-preflight-v1", "suite_id": suite["suite_id"],
              "suite_version": suite["version"], "cases": results, "counts": counts,
              "passed": all(item["verdict"] == "pass" for item in results),
              "model_calls": 0, "active_promotion": False, "release_eligible": False,
              "limitations": ["route execution and extractor calibration are separate acceptance stages",
                              "semantic terminal cases require an LLM judge calibration path"]}
    destination.mkdir(parents=True)
    write_json(destination / "preflight.json", report)
    return report
