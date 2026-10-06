"""Build v16 wire fixtures from existing scripted protocol-test intentions.

This is not a model-output adapter or a semantic judge. A bare scripted pass
contains no asserted claim; explicit findings retain their supplied locations.
"""

from copy import deepcopy

from evals.golden.judge_grounding import valid_counterevidence


def catalog_entries(payload):
    for group in payload["evidence_catalog"]:
        for entry in group["entries"]:
            yield {"ref_id": entry["ref_id"], "path": [*group["prefix"], entry["key"]]}


def catalog_fixture(references, payload):
    """Translate test-owned locations only; never repair actual model responses."""
    if references is None:
        return None
    result = []
    for reference in references:
        if set(reference) == {"ref_id"}:
            result.append(deepcopy(reference))
            continue
        entry = next((item for item in catalog_entries(payload)
            if item["path"] == reference.get("path") and valid_counterevidence([reference], payload)), None)
        result.append({"ref_id": entry["ref_id"] if entry else "unknown-test-reference"})
    return result


def bound_delivery_fixture(decisions, payload):
    result = deepcopy(decisions)
    for check in result.get("checks", []):
        if isinstance(check, dict) and "counterevidence" in check:
            check["counterevidence"] = catalog_fixture(check["counterevidence"], payload)
    return result


def bound_audit_fixture(decisions, payload):
    result = deepcopy(decisions)
    for dimension in ("factual", "source", "safety"):
        decision = result.get(dimension)
        if not isinstance(decision, dict) or "passed" in decision:
            continue
        if "claims" in decision:
            for claim in decision["claims"]:
                claim["evidence"] = catalog_fixture(claim["evidence"], payload)
            continue
        relation = decision.get("evidence_relation")
        if relation is None:
            continue  # Keep intentionally malformed protocol fixtures malformed.
        if relation == "no_claim" or relation == "supported" and not decision.get("quote"):
            result[dimension] = {"reason": decision["reason"], "claims": []}
            continue
        surface = decision.get("surface_id") if relation == "contradicted" else decision.get("surface_id") or "final"
        text = next((item["text"] for item in payload["surfaces"] if item["surface_id"] == surface), "")
        quote = decision.get("quote")
        if quote is None and relation != "contradicted":
            quote = text[:1200]
        target = decision.get("fixture_target") or {
            "factual": "world_fact", "source": "source_attribution", "safety": "safety"}[dimension]
        result[dimension] = {"reason": decision["reason"], "claims": [{
            "surface_id": surface, "quote": quote, "target": target,
            "evidence_relation": relation, "evidence": catalog_fixture(decision.get("counterevidence") or [], payload),
        }]}
    return result
