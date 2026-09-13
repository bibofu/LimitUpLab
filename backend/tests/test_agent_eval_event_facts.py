"""Calibration contract examples; synthetic labels are not human review records."""

from copy import deepcopy
import json

from langchain_core.messages import AIMessage
import pytest

from app.agent_eval.candidates import summary_candidate
from app.agent_eval.event_candidates import LocalEventsRegistry
from app.agent_eval.event_extractor import extract_event_answer
from app.agent_eval.event_facts import ListExtraction, verify_event_facts
from app.agent_eval.models import AssertionSpec
from app.agent_eval.recorder import capture_tool, digest
from app.services.sample_data import SAMPLE_EVENTS
from test_agent_eval_checks import ANCHOR, artifact, invoke, no_external_calls


@pytest.fixture
def bundle(artifact):
    case, world = summary_candidate(artifact)
    events = [SAMPLE_EVENTS[0].model_copy(update={"symbol": f"60000{i}", "name": f"样例{i}",
        "trade_date": ANCHOR.date(), "closed_limit": True, "amount": float(10-i)}) for i in range(3)]
    args = {"trade_date": "2026-09-11", "sort_by": "amount", "sort_order": "desc"}
    capture = capture_tool(LocalEventsRegistry(events), tool="limit_up_events", arguments=args,
        anchor_datetime=ANCHOR, recording_id="synthetic-events", provenance="synthetic contract only",
        source_manifest={"test_only": True})
    world.recordings = [capture.body.recording]
    expected = {"trade_date": "2026-09-11", "ordered_members": [
        {"symbol": e.symbol, "name": e.name} for e in events]}
    case.assertions = [AssertionSpec(id="list", evaluator="fact", kind="answer_matches_observation",
        target="answer.ordered_events", requirement_id="summary", expected=expected)]
    answer = "2026-09-11：样例0（600000）、样例1（600001）、样例2（600002）"
    response = invoke(world, tool="limit_up_events", args=args, answer=answer)
    extraction = ListExtraction(answer_digest=digest(answer), extractor_version="contract-test",
        origin="contract_test", quote=answer, start=0, trade_date=ANCHOR.date(),
        members=expected["ordered_members"], ambiguous=False, inventory_complete=True,
        inventory_reviewers=["synthetic-review"], extraction_reviewers=["synthetic-review"])
    return case, world, response, extraction


def test_reviewed_ordered_list_matches_complete_visible_evidence(bundle):
    result = verify_event_facts(*bundle)
    assert result.verdict == "pass" and result.claim_coverage == 1
    assert len(result.support_paths) == 3 and not result.release_eligible


@pytest.mark.parametrize("change", ["missing", "order", "duplicate", "extra", "name", "date"])
def test_reviewed_errors_are_not_silently_accepted(bundle, change):
    extraction = bundle[3]
    if change == "missing":
        extraction.members.pop()
    elif change == "order":
        extraction.members.reverse()
    elif change == "duplicate":
        extraction.members[1] = extraction.members[0]
    elif change == "extra":
        extraction.members.append(extraction.members[0])
    elif change == "name":
        extraction.members[0].name = "错名"
    else:
        extraction.trade_date = ANCHOR.replace(day=10).date()
    assert verify_event_facts(*bundle).verdict == "fail"


@pytest.mark.parametrize("change", ["absent", "unreviewed", "ambiguous", "hash", "span", "extra_claim",
                                    "world", "view", "history", "missing_date"])
def test_uncertainty_and_broken_evidence_abstain(bundle, change):
    case, world, response, extraction = bundle
    if change == "absent":
        extraction = None
    elif change == "unreviewed":
        extraction.inventory_reviewers = []
    elif change == "ambiguous":
        extraction.ambiguous = True
    elif change == "hash":
        extraction.answer_digest = "different"
    elif change == "span":
        extraction.start = 1
    elif change == "extra_claim":
        extraction.additional_claims = ["额外业务事实"]
    elif change == "world":
        case.assertions[0].expected["ordered_members"][0]["name"] = "错误标注"
    elif change == "missing_date":
        extraction.trade_date = None
    elif change == "view":
        trace = next(t for t in response.tool_results if t.name == "react_observe")
        trace.output["results"][0]["rows"][0]["name"] = "损坏"
    else:
        trace = next(t for t in response.tool_results if t.name == "react_execution")
        for record in trace.output["evidence"].values():
            record["evidence_scope"] = "conversation_history"
    assert verify_event_facts(case, world, response, extraction).verdict == "needs_review"


def test_name_only_and_formatting_variants_do_not_require_regex(bundle):
    case, world, response, extraction = bundle
    for member in extraction.members:
        member.symbol = None
    response.answer = "2026-09-11\n|股票|\n| 样例0 |\n| 样例1 |\n| 样例2 |"
    extraction.answer_digest = digest(response.answer)
    extraction.quote = response.answer
    assert verify_event_facts(*bundle).verdict == "pass"


def test_diagnostic_never_invents_review_or_coverage(bundle):
    bundle[3].inventory_complete = False
    bundle[3].inventory_reviewers = []
    result = verify_event_facts(*bundle, diagnostic_unreviewed=True)
    assert result.verdict == "needs_review" and result.claim_coverage is None


def test_preview_is_not_full_visibility_but_valid_pages_establish_it(bundle):
    from app.agents.react_runtime.evidence import EvidenceStore
    response = bundle[2]
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    key, record = next(iter(execution["evidence"].items()))
    store = EvidenceStore()
    eid = store.add(tool="limit_up_events", payload=record["payload"], state=record["result_state"],
                    arguments=record["arguments"], sources=record.get("sources"))
    trace = next(t for t in response.tool_results if t.name == "react_observe")
    original = deepcopy(trace.output["results"][0])
    first = {**original, **store.view(eid, offset=0, limit=2), "evidence_id": key}
    trace.output["results"] = [first]
    assert verify_event_facts(*bundle).verdict == "needs_review"
    second = {**original, **store.view(eid, offset=2, limit=1), "evidence_id": key}
    trace.output["results"].append(second)
    assert verify_event_facts(*bundle).verdict == "pass"


def test_p0_requires_two_reviewers(bundle):
    bundle[0].severity = "P0"
    assert verify_event_facts(*bundle).verdict == "needs_review"
    bundle[3].inventory_reviewers.append("second-synthetic-review")
    bundle[3].extraction_reviewers.append("second-synthetic-review")
    assert verify_event_facts(*bundle).verdict == "pass"


def test_extractor_is_answer_only_and_preserves_order(bundle):
    answer = bundle[2].answer
    class Provider:
        def generate_messages(self, messages, tools, **kwargs):
            assert json.loads(messages[-1].content)["answer"] == answer
            assert "expected" not in messages[-1].content
            return AIMessage(content="", tool_calls=[{"id": "extract", "name": "submit_event_list",
                "args": {"trade_date": "2026-09-11", "members": [{"name": "样例2", "symbol": "600002"}],
                         "additional_claim_line_ids": [], "ambiguous": False}}])
    result = extract_event_answer(Provider(), answer)
    assert result.members[0].symbol == "600002"
    assert not result.inventory_complete and not result.inventory_reviewers


@pytest.mark.parametrize("line_ids,valid", [([1, 0, 1], True), ([9], False), ([2], False), ([-1], False)])
def test_no_list_failure_claims_use_host_owned_lines(line_ids, valid):
    answer = "无法返回名单。\n查询失败，不能确认股票。\n\n仅用于研究。"
    class Provider:
        def generate_messages(self, messages, tools, **kwargs):
            return AIMessage(content="", tool_calls=[{"id": "extract", "name": "submit_event_list",
                "args": {"trade_date": None, "members": [], "additional_claim_line_ids": line_ids,
                         "ambiguous": True}}])
    if not valid:
        with pytest.raises(ValueError):
            extract_event_answer(Provider(), answer)
    else:
        result = extract_event_answer(Provider(), answer)
        assert result.members == [] and result.additional_claims == answer.splitlines()[:2]
        assert result.extractor_version == "answer-only-event-list-v2"


def test_larger_recorded_list_supports_top_n_not_reversed_prefix(bundle):
    case, world, response, extraction = bundle
    # Asking for the first two members of an observed three-member list is valid.
    case.assertions[0].expected["ordered_members"] = case.assertions[0].expected["ordered_members"][:2]
    extraction.members = extraction.members[:2]
    assert verify_event_facts(*bundle).verdict == "pass"
    case.assertions[0].expected["ordered_members"].reverse()
    assert verify_event_facts(*bundle).verdict == "needs_review"
