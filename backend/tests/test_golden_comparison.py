from copy import deepcopy

import pytest

from evals.golden.comparison import compare_reports


def report():
    manifest = {"suite_version": "v1.1", "dataset_hash": "same-data", "evaluator_hash": "same-evaluator",
                "trials": 1, "case_ids": ["a", "b"], "model": "fixture", "provider": "fixture",
                "judge": "model", "judge_human_calibrated": False, "effective_model_configuration": {},
                "profile": "extended", "react_deadline_seconds": "120", "libraries": {},
                "git_commit": "old", "production_hash": "old-code"}
    return {"mode": "live-model-frozen-tools", "manifest": manifest,
            "results": [{"case_id": name, "trial": 1, "completed": True,
                         "verdict": state, "definition": {"turns": ["one-turn"]},
                         "turns": [{"checks": []}]} for name, state in (("a", "pass"), ("b", "fail"))]}


def test_paired_results_allow_production_change_and_keep_both_directions():
    before = report()
    after = deepcopy(before)
    after["manifest"].update(git_commit="new", production_hash="new-code")
    after["results"][0]["verdict"] = "review"
    after["results"][1]["verdict"] = "pass"
    result = compare_reports(before, after)
    assert result["before"]["pass_rate"] == result["after"]["pass_rate"] == .5
    assert result["regressions"][0]["case_id"] == "a"
    assert result["improvements"][0]["case_id"] == "b"


@pytest.mark.parametrize("field", ["evaluator_hash", "dataset_hash", "model", "effective_model_configuration", "trials"])
def test_mixed_evaluator_or_model_cannot_claim_agent_improvement(field):
    before, after = report(), report()
    after["manifest"][field] = "changed"
    with pytest.raises(ValueError, match=field):
        compare_reports(before, after)


def test_manifest_alone_cannot_hide_changed_oracles():
    before, after = report(), report()
    after["results"][0]["definition"]["turns"][0] = "relaxed-oracle"
    with pytest.raises(ValueError, match="oracle changed"):
        compare_reports(before, after)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "incomplete", "turns", "old_manifest"])
def test_incomplete_or_legacy_reports_are_not_paired(mutation):
    before, after = report(), report()
    if mutation == "duplicate": after["results"].append(deepcopy(after["results"][0]))
    elif mutation == "missing": after["results"].pop()
    elif mutation == "incomplete": after["results"][0]["completed"] = False
    elif mutation == "turns": after["results"][0]["turns"] = []
    elif mutation == "old_manifest": after["manifest"].pop("evaluator_hash")
    with pytest.raises(ValueError):
        compare_reports(before, after)
