"""Carry an existing business approval across recording-only World revisions."""

import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest


APPROVED_ITEMS = {"question_and_business_scope", "expected_business_facts", "scoring_rules"}


def business_contract(case):
    """Fields a reviewer approved; runtime asset identity and World reference are excluded."""
    return {
        "profile": case.profile,
        "mode": case.mode,
        "severity": case.severity,
        "capabilities": case.capabilities,
        "conversation": [turn.model_dump(mode="json") for turn in case.conversation],
        "expected_requirements": [item.model_dump(mode="json") for item in case.expected_requirements],
        "assertions": [item.model_dump(mode="json") for item in case.assertions],
        "expected_terminal": case.expected_terminal.model_dump(mode="json"),
    }


def _entry(bundle: Path, case_id: str):
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    item = next((item for item in suite["cases"] if item["id"] == case_id), None)
    if item is None:
        raise ValueError("case absent from bundle")
    return suite, item, load_case(bundle / item["case"]), load_world(bundle / item["world"])


def carry_business_approval(source_bundle: Path, target_bundle: Path, approval_path: Path,
                            case_id: str, destination: Path):
    """Produce a machine attestation, never a new or expanded human approval."""
    if destination.exists():
        raise FileExistsError(destination)
    source_suite, source_item, source_case, source_world = _entry(source_bundle, case_id)
    target_suite, target_item, target_case, target_world = _entry(target_bundle, case_id)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approved = next((item for item in approval.get("cases", []) if item.get("case_id") == case_id), None)
    if (approval.get("reviewer") != "user_in_current_conversation" or approved is None
            or not APPROVED_ITEMS <= set(approval.get("approved_items", []))
            or approved.get("case_digest") != digest(source_case.model_dump(mode="json"))
            or approved.get("baseline_digest") != digest(source_world.model_dump(mode="json"))):
        raise ValueError("source business approval is missing or stale")
    source_contract, target_contract = business_contract(source_case), business_contract(target_case)
    if source_contract != target_contract:
        raise ValueError("reviewed business contract changed")
    if target_item.get("oracle_crosscheck") is not True:
        raise ValueError("target baseline lacks independent oracle crosscheck")
    report = {
        "schema_version": "business-approval-carry-forward-v1",
        "case_id": case_id,
        "contract_digest": digest(source_contract),
        "source": {"suite_id": source_suite["suite_id"], "suite_version": source_suite["version"],
                   "case_digest": approved["case_digest"], "baseline_digest": approved["baseline_digest"],
                   "approval_digest": digest(approval)},
        "target": {"suite_id": target_suite["suite_id"], "suite_version": target_suite["version"],
                   "case_digest": digest(target_case.model_dump(mode="json")),
                   "baseline_digest": digest(target_world.model_dump(mode="json"))},
        "equivalence": {"business_contract_unchanged": True, "target_oracle_crosscheck": True,
                        "change_scope": "recording routes and runtime asset identity only"},
        "origin": "deterministic carry-forward of an existing user approval; not a new human review",
        "active_promotion": False,
        "release_eligible": False,
    }
    write_json(destination, report)
    return report
