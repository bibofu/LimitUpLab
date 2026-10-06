"""Build v15 wire fixtures from existing scripted protocol-test intentions.

This is not a model-output adapter or a semantic judge. A bare scripted pass
contains no asserted claim; explicit findings retain their supplied locations.
"""

from copy import deepcopy


def bound_audit_fixture(decisions, payload):
    result = deepcopy(decisions)
    for dimension in ("factual", "source", "safety"):
        decision = result.get(dimension)
        if not isinstance(decision, dict) or "claims" in decision or "passed" in decision:
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
            "evidence_relation": relation, "evidence": decision.get("counterevidence") or [],
        }]}
    return result
