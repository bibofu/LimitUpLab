"""Acceptance gate tests use synthetic approvals, never persisted user approvals."""

import json

import pytest
from langchain_core.messages import AIMessage

from app.agent_eval.highest_acceptance import accept_highest, calibration_samples, promote_highest
from app.agent_eval.recorder import digest
from test_agent_eval_business_facts import specimen
from test_agent_eval_core_batch import batch


def prepare(specimen,tmp_path):
    case,world,_,_=specimen("OFF-010")
    folder=tmp_path/"review"
    folder.mkdir()
    for name,model in (("case",case),("baseline",world)):
        (folder/(name+".json")).write_text(model.model_dump_json(),encoding="utf-8")
    approval={"case_id":case.case_id,"case_digest":digest(case.model_dump(mode="json")),
        "baseline_digest":digest(world.model_dump(mode="json")),"reviewer":"user_in_current_conversation",
        "approved_items":["question_and_business_scope","expected_business_facts","scoring_rules"]}
    path=folder/"synthetic-approval.json"
    path.write_text(json.dumps(approval),encoding="utf-8")
    labels={text:label for _,text,label in calibration_samples(case.assertions[0].expected)}
    class Provider:
        model="synthetic-model"
        calls=0
        def generate_messages(self,messages,tools,**kwargs):
            self.calls+=1
            payload=json.loads(messages[-1].content)
            assert set(payload)=={"answer","lines"}
            return AIMessage(content="",tool_calls=[{"id":str(self.calls),"name":"submit_event_list",
                "args":{**labels[payload["answer"]],"additional_claim_line_ids":[],"ambiguous":False}}])
    return folder,path,Provider()


def test_routes_and_templates_pass_without_automatic_promotion(specimen,tmp_path):
    folder,approval,provider=prepare(specimen,tmp_path)
    result=accept_highest(folder,approval,tmp_path/"acceptance",provider)
    assert result["technical_acceptance"] and not result["active_promotion"]
    assert provider.calls==6
    report=json.loads((tmp_path/"acceptance/acceptance.json").read_text(encoding="utf-8"))
    assert len(report["routes"])==3
    assert "not human answer annotations" in report["label_origin"]
    assert not report["release_eligible"]
    manifest=promote_highest(folder,approval,tmp_path/"acceptance/acceptance.json",tmp_path/"golden")
    assert manifest["status"]=="active" and not manifest["answer_quality_approved"]
    assert json.loads((folder/"case.json").read_text(encoding="utf-8"))["status"]=="candidate"
    assert json.loads((tmp_path/"golden/case.json").read_text(encoding="utf-8"))["status"]=="active"
    report["calibration"][0]["passed"]=False
    (tmp_path/"acceptance/acceptance.json").write_text(json.dumps(report),encoding="utf-8")
    with pytest.raises(ValueError,match="calibration"):
        promote_highest(folder,approval,tmp_path/"acceptance/acceptance.json",tmp_path/"invalid")
    assert not (tmp_path/"invalid").exists()


def test_stale_approval_prevents_route_and_model_work(specimen,tmp_path):
    folder,approval,provider=prepare(specimen,tmp_path)
    data=json.loads(approval.read_text())
    data["case_digest"]="stale"
    approval.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="approval"):
        accept_highest(folder,approval,tmp_path/"acceptance",provider)
    assert provider.calls==0 and not (tmp_path/"acceptance").exists()
