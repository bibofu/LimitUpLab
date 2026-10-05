"""Citation checks prove location/value agreement, never semantic correctness."""

import pytest
from pydantic import ValidationError

from evals.golden.judge_grounding import EvidenceReference, valid_counterevidence


@pytest.mark.parametrize("value", [False, 0, 0.0, None, [], "", ["2026-09-22"], {"complete": True}])
def test_exact_json_values_including_empty_results_are_not_treated_as_missing(value):
    payload = {"synthetic_evidence": {"current": {"value": value}}}
    citation = EvidenceReference(path=["synthetic_evidence", "current", "value"], value=value)
    assert valid_counterevidence([citation.model_dump()], payload)


@pytest.mark.parametrize("actual,cited", [(False, 0), (True, 1), (0, "0"), (1.0, 1),
                                          ([False], [0]), ({"flag": False}, {"flag": 0})])
def test_counterevidence_preserves_nested_json_types(actual, cited):
    payload = {"business_observations": [{"output": actual}]}
    citation = EvidenceReference(path=["business_observations", 0, "output"], value=cited)
    assert not valid_counterevidence([citation.model_dump()], payload)


@pytest.mark.parametrize("path", [["business_observations", True, "output"],
                                   ["synthetic_evidence", "current"],
                                   ["business_observations", 0.0, "output"]])
def test_reference_schema_rejects_ambiguous_indices_and_whole_record_citations(path):
    with pytest.raises(ValidationError):
        EvidenceReference(path=path, value="anything")


def test_runtime_date_reference_is_checked_against_supplied_metadata():
    citation = EvidenceReference(path=["trusted_runtime_metadata", 0, "context", "available_local_dates"],
                                 value=["2026-09-22"])
    payload = {"trusted_runtime_metadata": [{"context": {"available_local_dates": ["2026-09-22"]}}]}
    assert valid_counterevidence([citation.model_dump()], payload)
    assert not valid_counterevidence([citation.model_dump()], {"trusted_runtime_metadata": []})
