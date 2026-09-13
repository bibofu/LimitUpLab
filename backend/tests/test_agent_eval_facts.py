"""Verifier/normalizer calibration seeds, not calibrated automatic extraction."""

from decimal import Decimal
import json

import pytest

from app.agent_eval.candidates import summary_candidate
from app.agent_eval.facts import Extraction, normalize_number, verify_summary_facts
from app.agent_eval.recorder import digest
from test_agent_eval_checks import artifact, invoke, no_external_calls


def annotated(response, *, value, certainty="exact", unit="家", metric="limit_up_count", day="2026-09-11"):
    return Extraction.model_validate({
        "answer_digest": digest(response.answer), "extractor_version": "manual-contract-fixture-v1",
        "origin": "contract_test", "inventory_complete": True,
        "inventory_reviewers": ["test-reviewer"], "extraction_reviewers": ["test-reviewer"],
        "inventory": [{"id": "count", "start": 0, "end": len(response.answer), "quote": response.answer,
                       "numeric": True, "relational": True}],
        "claims": [{"slot_id": "count", "entity": "A-share-market", "trade_date": day,
                    "metric": metric, "value_text": str(value), "unit": unit, "certainty": certainty}],
    })


def setup(artifact, text=None):
    case, world = summary_candidate(artifact)
    count = world.recordings[0].observation.payload["limit_up_count"]
    response = invoke(world, answer=text or f"2026-09-11 涨停 {count} 家。")
    return case, world, response, count


@pytest.mark.parametrize("style", ["plain", "spaces", "table", "ten_thousand", "parallel"])
def test_reviewed_normal_expression_variants_are_not_false_killed(artifact, style):
    case, world, _, count = setup(artifact)
    values = {"plain": (f"2026-09-11 涨停{count}家。", str(count), "家"),
              "spaces": (f"2026-09-11：涨停 {count} 只。", f" {count} ", "只"),
              "table": (f"|日期|涨停家数|\n|2026-09-11|{count}|", str(count), "家"),
              "ten_thousand": (f"2026-09-11 涨停{Decimal(count)/10000}万家。", str(Decimal(count)/10000), "万家"),
              "parallel": (f"2026-09-11，涨停{count}家；其余指标暂不展开。", str(count), "家")}
    answer, token, unit = values[style]
    response = invoke(world, answer=answer)
    report = verify_summary_facts(case, world, response, annotated(response, value=token, unit=unit))
    assert report.verdict == "pass" and not report.release_eligible
    assert report.claim_coverage == report.numeric_coverage == report.relation_coverage == 1
    assert any("/metadata/limit_up_count" in path for path in report.support_paths["count"])
    assert any("/rows/0/limit_up_count" in path for path in report.support_paths["count"])


@pytest.mark.parametrize("change", ["value", "date", "entity", "dimension"])
def test_reviewed_wrong_facts_fail_without_guessing_from_prose(artifact, change):
    case, world, response, count = setup(artifact)
    extraction = annotated(response, value=count)
    claim = extraction.claims[0]
    if change == "value":
        claim.value_text = str(count + 1)
    elif change == "date":
        claim.trade_date = claim.trade_date.replace(day=10)
    elif change == "entity":
        claim.entity = "some-stock"
    else:
        claim.unit = "亿元"
    # This intentionally inconsistent annotation tests only the verifier boundary.
    assert verify_summary_facts(case, world, response, extraction).verdict == "fail"


@pytest.mark.parametrize("certainty,token", [("approximate", "40"), ("uncertain", "40"),
                                           ("exact", "四十"), ("exact", "4,000")])
def test_approximation_negation_and_unparsed_tokens_require_review(artifact, certainty, token):
    case, world, response, _ = setup(artifact, "约四十家，并非精确值。")
    report = verify_summary_facts(case, world, response, annotated(response, value=token, certainty=certainty))
    assert report.verdict == "needs_review"


@pytest.mark.parametrize("failure", ["absent", "zero", "partial", "digest", "span", "review", "p0"])
def test_extraction_failures_and_coverage_cannot_silently_pass(artifact, failure):
    case, world, response, count = setup(artifact)
    extraction = annotated(response, value=count)
    if failure == "absent":
        extraction = None
    elif failure == "zero":
        extraction.claims = []
    elif failure == "partial":
        extra = extraction.inventory[0].model_copy(update={"id": "unextracted"})
        extraction.inventory.append(extra)
    elif failure == "digest":
        extraction.answer_digest = "another-answer"
    elif failure == "span":
        extraction.inventory[0].quote = "not in answer"
    elif failure == "review":
        extraction.inventory_complete = False
    else:
        case.severity = "P0"
    report = verify_summary_facts(case, world, response, extraction)
    assert report.verdict == "needs_review"
    if failure in {"zero", "partial"}:
        assert report.claim_coverage == (0 if failure == "zero" else .5)


def test_world_correct_claim_without_query_is_unsupported(artifact):
    case, world, _, count = setup(artifact)
    response = invoke(world, tool=None, answer=f"2026-09-11 涨停{count}家。")
    report = verify_summary_facts(case, world, response, annotated(response, value=count))
    assert report.verdict == "fail"
    assert any("lacks current visible" in finding.detail for finding in report.findings)


@pytest.mark.parametrize("change", ["observations", "visible_value", "hidden_metadata", "current_value", "world_value", "scope"])
def test_view_record_or_world_drift_is_review_not_false_agent_failure(artifact, change):
    case, world, response, count = setup(artifact)
    extraction = annotated(response, value=count)
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    record = next(iter(execution["evidence"].values()))
    observed = next(t.output["results"][0] for t in response.tool_results if t.name == "react_observe")
    if change == "observations":
        response.tool_results = [t for t in response.tool_results if t.name != "react_observe"]
    elif change == "visible_value":
        observed["metadata"]["limit_up_count"] = 999
    elif change == "hidden_metadata":
        observed["metadata"].pop("limit_up_count")
    elif change == "current_value":
        record["payload"]["limit_up_count"] = 999
    elif change == "scope":
        record.pop("evidence_scope")
    else:
        world.recordings[0].observation.payload["limit_up_count"] = 999
    assert verify_summary_facts(case, world, response, extraction).verdict == "needs_review"


def test_history_does_not_become_current_grounding(artifact):
    case, world, response, count = setup(artifact)
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    record = next(iter(execution["evidence"].values()))
    record.update(evidence_scope="conversation_history", historical_reference=True)
    assert verify_summary_facts(case, world, response, annotated(response, value=count)).verdict == "fail"


def test_nonrequired_claim_is_checked_against_tool_truth_not_only_expected_facts(artifact):
    case, world, response, count = setup(artifact)
    extraction = annotated(response, value=count)
    slot = extraction.inventory[0].model_copy(update={"id": "extra"})
    extraction.inventory.append(slot)
    claim = extraction.claims[0].model_copy(update={"slot_id": "extra", "metric": "first_board_count", "value_text": "999"})
    extraction.claims.append(claim)
    assert verify_summary_facts(case, world, response, extraction).verdict == "fail"


@pytest.mark.parametrize("token,unit,family,expected", [("1.2", "亿元", "money", "120000000"),
    ("12000", "万元", "money", "120000000"), ("-1.5", "%", "percent", "-1.5"),
    ("1.5", "百分点", "percentage_point", "1.5"), ("0.004", "万家", "count", "40"),
    ("40.00000000000000000000000000001", "家", "count", "40.00000000000000000000000000001")])
def test_unit_normalizer_preserves_dimensions_and_exact_precision(token, unit, family, expected):
    assert normalize_number(token, unit) == (family, Decimal(expected))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "4_0", "4 0", "1e1000", "约40"])
def test_ambiguous_or_unsafe_numeric_tokens_are_not_coerced(token):
    with pytest.raises(ValueError):
        normalize_number(token, "家")


def test_actual_wrong_answer_with_matching_annotation_fails(artifact):
    case, world, _, count = setup(artifact)
    response = invoke(world, answer=f"2026-09-11 涨停{count + 1}家。")
    assert verify_summary_facts(case, world, response, annotated(response, value=count + 1)).verdict == "fail"


def test_p0_requires_distinct_reviewers_and_span_must_be_in_bounds(artifact):
    case, world, response, count = setup(artifact)
    case.severity = "P0"
    extraction = annotated(response, value=count)
    extraction.inventory_reviewers *= 2
    extraction.extraction_reviewers *= 2
    assert verify_summary_facts(case, world, response, extraction).verdict == "needs_review"
    extraction.inventory_reviewers = extraction.extraction_reviewers = ["test-reviewer-1", "test-reviewer-2"]
    assert verify_summary_facts(case, world, response, extraction).verdict == "pass"
    extraction.inventory[0].end += 100
    assert verify_summary_facts(case, world, response, extraction).verdict == "needs_review"


@pytest.mark.parametrize("with_extraction,expected_exit", [(True, 0), (False, 2)])
def test_fact_cli_requires_extraction_and_keeps_scope_explicit(artifact, tmp_path, monkeypatch, capsys, with_extraction, expected_exit):
    from app.agent_eval.__main__ import main

    case, world, response, count = setup(artifact)
    extraction = annotated(response, value=count)
    for name, model in [("case", case), ("world", world), ("response", response), ("extraction", extraction)]:
        (tmp_path / (name + ".json")).write_text(model.model_dump_json(), encoding="utf-8")
    argv = ["agent_eval", "check-summary-facts"]
    for name in ["case", "world", "response"] + (["extraction"] if with_extraction else []):
        argv += ["--" + name, str(tmp_path / (name + ".json"))]
    monkeypatch.setattr("sys.argv", argv)
    assert main() == expected_exit
    report = json.loads(capsys.readouterr().out)
    assert report["scope"] == "reviewed_summary_numeric_claims" and not report["release_eligible"]
