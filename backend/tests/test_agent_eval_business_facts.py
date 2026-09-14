"""Positive and adversarial business labels, all synthetic and never human approvals."""

import pytest

from app.agent_eval.business_facts import BusinessExtraction, verify_business_facts
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest
from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.services.prompt_security import PromptInjectionAssessment
from test_agent_eval_core_batch import batch
from test_agent_eval_checks import invoke


@pytest.mark.parametrize("damage", [None, "wrong_day", "wrong_market", "query", "error", "source_error", "rows", "truncated", "wrong_type", "nonzero"])
def test_empty_selection_requires_exact_usable_observation(damage):
    from app.agent_eval.business_facts import empty_selection_supported
    expected = {"trade_date": "2026-09-11", "row_selection": {"closed_limit": True, "market": "star_market"}}
    record = {"tool": "limit_up_events", "result_state": "empty", "arguments": {}, "payload": {"events": []}}
    view = {"metadata": {"trade_date": "2026-09-11", "market": "star_market", "event_status": "closed", "matched_count": 0, "returned_count": 0}, "rows": []}
    if damage == "wrong_day": view["metadata"]["trade_date"] = "2026-09-10"
    if damage == "wrong_market": view["metadata"]["market"] = "main_board"
    if damage == "query": record["arguments"]["query"] = "other"
    if damage == "error": record["result_state"] = "error"
    if damage == "source_error": record["payload"]["source_errors"] = ["unavailable"]
    if damage == "rows": view["rows"] = [{"symbol": "688001"}]
    if damage == "truncated": view["source_truncated"] = True
    if damage == "wrong_type": view["metadata"]["event_status"] = "failed"
    if damage == "nonzero": view["metadata"]["matched_count"] = 1
    assert empty_selection_supported(expected, record, view) is (damage is None)
    record["tool"] = "market_event_pool"
    view["metadata"]["event_type"] = "broken_board" if damage == "wrong_type" else "limit_up"
    assert empty_selection_supported(expected, record, view) is (damage is None)


@pytest.fixture
def specimen(batch, monkeypatch):
    _, folder, _ = batch
    monkeypatch.setattr(runtime,"review_answer",lambda *a,**k: ComplianceReview(decision="allow",violations=[],reason="test"))
    monkeypatch.setattr(runtime,"review_input",lambda *a,**k: PromptInjectionAssessment(decision="allow",signals=[],reason="test"))
    def make(key):
        case = load_case(folder / key / "case.json")
        world = load_world(folder / key / ("baseline.json" if key=="LH-004" else "world.json"))
        expected = case.assertions[0].expected
        args = {"trade_date":expected["trade_date"],"limit":100}
        if key == "OFF-010": args["highest_only"] = True
        if key == "OFF-028": args.update(market="star_market",event_status="broken_intraday")
        if key == "OFF-033": args["query"] = "评测不存在主题"
        answer = "合成标签对应的答案，仅用于契约测试。"
        response = invoke(world,tool="limit_up_events",args=args,answer=answer)
        extraction = BusinessExtraction(answer_digest=digest(answer),extractor_version="synthetic-label",
            origin="contract_test",quote=answer,start=0,trade_date=expected["trade_date"],ambiguous=False,
            members=[{"symbol":m["symbol"],"name":m["name"]} for m in expected.get("members",[])],
            inventory_complete=True,inventory_reviewers=["synthetic-review"],extraction_reviewers=["synthetic-review"],
            **{k:expected[k] for k in ("max_board_height","limit_up_count","matched_count") if k in expected})
        return case,world,response,extraction
    return make


@pytest.mark.parametrize("key",["OFF-010","OFF-028","OFF-031","OFF-033","LH-004"])
def test_business_requirements_can_be_scored(specimen,key):
    report = verify_business_facts(*specimen(key))
    assert report.verdict == "pass" and not report.release_eligible


@pytest.mark.parametrize("mutation",["missing_tie","duplicate","wrong_height","wrong_day"])
def test_incorrect_highest_answer_fails(specimen,mutation):
    values = specimen("OFF-010")
    extraction = values[-1]
    if mutation=="missing_tie": extraction.members.pop()
    elif mutation=="duplicate": extraction.members.append(extraction.members[0])
    elif mutation=="wrong_height": extraction.max_board_height=99
    else: extraction.trade_date=extraction.trade_date.replace(day=11)
    assert verify_business_facts(*values).verdict == "fail"


def test_reclosure_omission_is_detected(specimen):
    values=specimen("OFF-028")
    values[-1].members.pop()
    assert verify_business_facts(*values).verdict == "fail"


def test_latest_count_cannot_replace_historical_count(specimen):
    values=specimen("OFF-031")
    values[-1].limit_up_count=3
    assert verify_business_facts(*values).verdict == "fail"


def test_empty_members_do_not_vacuously_prove_market_scope(specimen, monkeypatch):
    values = specimen("OFF-033")
    values[0].assertions[0].expected["row_selection"] = {"closed_limit": True, "market": "star_market"}
    # Isolate evidence grounding: the old query's empty observation cannot prove STAR emptiness.
    monkeypatch.setattr("app.agent_eval.business_facts._truth", lambda *args: ({"matched_count": 0}, []))
    report = verify_business_facts(*values)
    assert report.verdict == "needs_review"
    assert any(f.detail == "current evidence support not established" for f in report.findings)


@pytest.mark.parametrize("damage",["world","hash","unreviewed","ambiguous","additional"])
def test_uncertainty_does_not_become_agent_failure(specimen,damage):
    case,world,response,extraction=specimen("OFF-010")
    if damage=="world": case.assertions[0].expected["max_board_height"]=99
    elif damage=="hash": extraction.answer_digest="wrong"
    elif damage=="unreviewed": extraction.inventory_reviewers=[]
    elif damage=="ambiguous": extraction.ambiguous=True
    else: extraction.additional_claims=["未校验额外事实"]
    assert verify_business_facts(case,world,response,extraction).verdict == "needs_review"


def test_model_inventory_alone_never_promotes_quality(specimen):
    values=specimen("OFF-010")
    values[-1].inventory_complete=False
    values[-1].inventory_reviewers=[]
    result=verify_business_facts(*values,diagnostic_unreviewed=True)
    assert result.verdict=="needs_review" and result.claim_coverage is None


def test_review_packet_does_not_forge_approval_or_leak_expected_to_extractor(specimen,tmp_path):
    import json
    from langchain_core.messages import AIMessage
    from app.agent_eval.golden_review import review_business_run
    case,world,response,extraction=specimen("OFF-010")
    run=tmp_path/"run"
    run.mkdir()
    for name,model in (("case",case),("world",world),("response",response)):
        (run/(name+".json")).write_text(model.model_dump_json(),encoding="utf-8")
    class Provider:
        model="synthetic-provider"
        def generate_messages(self,messages,tools,**kwargs):
            supplied=json.loads(messages[-1].content)
            assert set(supplied)=={"answer","lines"}
            return AIMessage(content="",tool_calls=[{"id":"extract","name":"submit_event_list",
                "args":{"trade_date":None,"members":[],"additional_claim_line_ids":[0],
                        "ambiguous":True}}])
    target=tmp_path/"review"
    result=review_business_run(run,target,Provider())
    approval=json.loads((target/"review.json").read_text(encoding="utf-8"))
    assert result["review_status"]=="pending_human_review"
    assert approval["reviewer"] is None and not approval["approved"]
    assert not any(approval["checks"].values()) and not approval["release_eligible"]
    assert (target/"REVIEW.md").exists()
    with pytest.raises(FileExistsError):
        review_business_run(run,target,Provider())
