"""Technical acceptance for user-approved highest-board cases; no invented human labels."""

from pathlib import Path
from time import perf_counter
import json

from app.agent_eval.business_facts import _source_rows, _truth
from app.agent_eval.core_batch import write_json
from app.agent_eval.event_extractor import BUSINESS_SYSTEM, extract_business_answer
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.historical_live import HistoricalLiveRegistry
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.models import BudgetSpec
from app.agent_eval.recorder import digest
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway


def calibration_samples(expected):
    """Deterministic variants of approved facts, not observed human-written answers."""
    day, height, members = expected["trade_date"], expected["max_board_height"], expected["members"]
    def sentence(items, value):
        return f"{day}最高连板为{value}板：" + "、".join(f"{m['name']}（{m['symbol']}）" for m in items) + "。"
    base = {"trade_date":day,"max_board_height":height,"members":members}
    return [
        ("plain",sentence(members,height),base),
        ("reverse",sentence(list(reversed(members)),height),{**base,"members":list(reversed(members))}),
        ("table",f"交易日：{day}。最高连板高度：{height} 板。\n|代码|名称|\n|---|---|\n" +
         "\n".join(f"| {m['symbol']} | {m['name']} |" for m in members),base),
        ("wrong_height",sentence(members,height+1),{**base,"max_board_height":height+1}),
        ("omission",sentence(members[:-1],height),{**base,"members":members[:-1]}),
        ("duplicate",sentence(members+[members[0]],height),{**base,"members":members+[members[0]]}),
    ]


def accept_highest(review_directory: Path, approval_path: Path, destination: Path, provider, *, database=None):
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    case = load_case(review_directory / "case.json")
    baseline = load_world(review_directory / "baseline.json")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if (approval.get("case_id") != case.case_id or approval.get("case_digest") != digest(case.model_dump(mode="json"))
            or approval.get("baseline_digest") != digest(baseline.model_dump(mode="json"))
            or approval.get("reviewer") != "user_in_current_conversation"
            or not {"question_and_business_scope","expected_business_facts","scoring_rules"} <= set(approval.get("approved_items",[]))):
        raise ValueError("missing or mismatched user business approval")
    if case.status != "candidate" or len(case.assertions) != 1:
        raise ValueError("highest acceptance supports one candidate business assertion")
    expected = case.assertions[0].expected
    if case.assertions[0].target != "answer.business_contract" or set(expected) != {"trade_date","max_board_height","members"}:
        raise ValueError("unsupported highest contract")
    scalars, members = _truth(expected,baseline,_source_rows(baseline))
    identities = {(m["symbol"],m["name"]) for m in expected["members"]}
    if scalars["max_board_height"] != expected["max_board_height"] or identities != {(r["symbol"],r["name"]) for r in members}:
        raise ValueError("approved oracle differs from complete baseline")
    if case.mode == "live_historical":
        if database is None:
            raise ValueError("Live acceptance requires a readonly source database")
        registry = HistoricalLiveRegistry(database,baseline)
    elif case.mode == "offline":
        registry = FrozenAgentToolRegistry(baseline)
    else:
        raise ValueError("unsupported mode")
    # Direct highest and complete-pool routes, each with default/expanded limits.
    routes=[]
    for highest,limit in ((True,30),(True,100),(False,100)):
        args={"trade_date":expected["trade_date"],"limit":limit}
        if highest: args["highest_only"]=True
        with registry.anchored():
            gateway=ToolGateway(registry,EvidenceStore())
            validated=gateway.validate({"name":"limit_up_events","args":args})
            _,payload,_=gateway.execute("limit_up_events",validated)
        rows=payload["events"]
        if not rows or payload["matched_count"] != payload["returned_count"]:
            raise ValueError("route does not provide a complete pool")
        peak=max(r["board_height"] for r in rows)
        selected={(r["symbol"],r["name"]) for r in rows if r["board_height"]==peak}
        if peak != expected["max_board_height"] or selected != identities:
            raise ValueError("equivalent route changes highest oracle")
        routes.append({"arguments":args,"passed":True})
    destination.mkdir(parents=True)
    samples=calibration_samples(expected)
    budget=BudgetSpec(max_agent_runs=1,max_model_calls=len(samples),max_input_tokens=200000,
        max_output_tokens=40000,max_wall_time_seconds=240,max_estimated_cost_usd=None)
    guarded=GuardedProvider(provider,destination,perf_counter()+240,budget)
    results=[]
    with capture_llm_usage() as usage:
        for name,answer,label in samples:
            try:
                extracted=extract_business_answer(guarded,answer)
                actual={"trade_date":extracted.trade_date.isoformat() if extracted.trade_date else None,
                        "max_board_height":extracted.max_board_height,
                        "members":[m.model_dump() for m in extracted.members]}
                passed=actual==label and (not extracted.ambiguous or not label["members"])
                record={"sample":name,"answer":answer,"label":label,"actual":actual,"passed":passed,
                        "extraction":extracted.model_dump(mode="json")}
            except Exception as error:
                record={"sample":name,"answer":answer,"label":label,"passed":False,"error_type":type(error).__name__}
            results.append(record)
    passed=all(r["passed"] for r in results)
    report={"case_id":case.case_id,"case_digest":approval["case_digest"],"baseline_digest":approval["baseline_digest"],
        "approval_digest":digest(approval),"routes":routes,"calibration":results,
        "label_origin":"deterministic variants of user-approved facts, not human answer annotations",
        "extractor_prompt_digest":digest(BUSINESS_SYSTEM),"model":getattr(provider,"model",None),
        "model_calls":guarded.calls,"total_tokens":usage.total_tokens if usage.token_usage_complete else None,
        "technical_acceptance":passed,"active_promotion":False,"release_eligible":False,
        "scope":"highest-board requirements only; additional claims and full-model quality not calibrated"}
    write_json(destination/"acceptance.json",report)
    return {k:report[k] for k in ("case_id","technical_acceptance","model_calls","total_tokens","active_promotion")}


def promote_highest(review_directory: Path, approval_path: Path, acceptance_path: Path, destination: Path):
    """Activate a scoped case, not an answer grade or a global release gate."""
    if destination.exists():
        raise FileExistsError(destination)
    case=load_case(review_directory/"case.json")
    baseline=load_world(review_directory/"baseline.json")
    approval=json.loads(approval_path.read_text(encoding="utf-8"))
    acceptance=json.loads(acceptance_path.read_text(encoding="utf-8"))
    case_hash=digest(case.model_dump(mode="json"))
    baseline_hash=digest(baseline.model_dump(mode="json"))
    if case.status != "candidate" or case.severity != "P1" or len(case.assertions)!=1:
        raise ValueError("only the reviewed P1 single-contract candidates may be promoted")
    if case.assertions[0].target != "answer.business_contract" or "max_board_height" not in case.assertions[0].expected:
        raise ValueError("unsupported promotion scope")
    if (approval.get("reviewer")!="user_in_current_conversation" or approval.get("case_id")!=case.case_id
            or not {"question_and_business_scope","expected_business_facts","scoring_rules"} <= set(approval.get("approved_items",[]))):
        raise ValueError("business approval incomplete")
    for record in (approval,acceptance):
        if record.get("case_digest")!=case_hash or record.get("baseline_digest")!=baseline_hash:
            raise ValueError("approval or technical acceptance is stale")
    if (acceptance.get("approval_digest")!=digest(approval) or acceptance.get("technical_acceptance") is not True
            or acceptance.get("extractor_prompt_digest")!=digest(BUSINESS_SYSTEM)):
        raise ValueError("technical acceptance is incomplete or stale")
    expected_samples=calibration_samples(case.assertions[0].expected)
    results=acceptance.get("calibration",[])
    if len(results)!=len(expected_samples):
        raise ValueError("calibration samples missing")
    for result,(name,text,label) in zip(results,expected_samples):
        if (result.get("sample")!=name or result.get("answer")!=text or result.get("label")!=label
                or result.get("actual")!=label or result.get("passed") is not True):
            raise ValueError("calibration failed or labels changed")
    day=case.assertions[0].expected["trade_date"]
    route_args=[{"trade_date":day,"limit":30,"highest_only":True},
                {"trade_date":day,"limit":100,"highest_only":True},{"trade_date":day,"limit":100}]
    if acceptance.get("routes") != [{"arguments":args,"passed":True} for args in route_args]:
        raise ValueError("required equivalent routes not verified")
    active=case.model_copy(update={"status":"active"})
    destination.mkdir(parents=True)
    write_json(destination/"case.json",active.model_dump(mode="json"))
    write_json(destination/("world.json" if case.mode=="offline" else "baseline.json"),baseline.model_dump(mode="json"))
    write_json(destination/"business-approval.json",approval)
    write_json(destination/"technical-acceptance.json",acceptance)
    manifest={"case_id":case.case_id,"case_version":case.case_version,"mode":case.mode,"status":"active",
        "scope":"highest-board core requirements", "approved_candidate_digest":case_hash,
        "active_case_digest":digest(active.model_dump(mode="json")),"baseline_digest":baseline_hash,
        "approval_digest":digest(approval),"acceptance_digest":digest(acceptance),
        "release_eligible":False,"answer_quality_approved":False,
        "limitations":["automatic extraction outside tested templates remains provisional",
                       "additional business statements require separate review",
                       "unrecorded valid offline routes yield unscorable fixture_failure",
                       "historical Live is bounded to two local tools, not the complete profile",
                       "local data assets are not distributed through git"]}
    write_json(destination/"manifest.json",manifest)
    return manifest
