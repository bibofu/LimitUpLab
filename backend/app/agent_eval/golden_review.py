"""Prepare an immutable human-review packet; never fabricate approvals or active cases."""

import json
from pathlib import Path
from time import perf_counter

from app.agent_eval.business_facts import verify_business_facts
from app.agent_eval.core_batch import write_json
from app.agent_eval.event_extractor import extract_business_answer, BUSINESS_SYSTEM
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.models import BudgetSpec
from app.agent_eval.recorder import digest
from app.models import AgentChatResponse


def review_business_run(run_directory: Path, destination: Path, provider):
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    case = load_case(run_directory / "case.json")
    if not any(a.target == "answer.business_contract" and isinstance(a.expected, dict)
               and "max_board_height" in a.expected for a in case.assertions):
        raise ValueError("this review packet currently supports highest-board contracts only")
    baseline_name = "world.json" if case.mode == "offline" else "baseline.json"
    baseline = load_world(run_directory / baseline_name)
    response = AgentChatResponse.model_validate_json((run_directory / "response.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=False)
    budget = BudgetSpec(max_agent_runs=1,max_model_calls=1,max_input_tokens=100000,
        max_output_tokens=12000,max_wall_time_seconds=90,max_estimated_cost_usd=None)
    guarded = GuardedProvider(provider,destination,perf_counter()+90,budget)
    with capture_llm_usage() as usage:
        extraction = extract_business_answer(guarded,response.answer)
    report = verify_business_facts(case,baseline,response,extraction,diagnostic_unreviewed=True)
    for name, value in (("case",case),("baseline",baseline),("extraction",extraction),("facts",report)):
        write_json(destination / (name+".json"),value.model_dump(mode="json"))
    bindings={"case_digest":digest(case.model_dump(mode="json")),
              "baseline_digest":digest(baseline.model_dump(mode="json")),"answer_digest":digest(response.answer)}
    write_json(destination / "review.json", {**bindings,"reviewer":None,"approved":False,
        "checks":{"requirements":False,"oracle":False,"allowed_routes":False,"calibration":False},
        "review_status":"pending_human_review","release_eligible":False,
        "extractor_prompt_digest":digest(BUSINESS_SYSTEM),"model":getattr(provider,"model",None),
        "model_calls":guarded.calls,"total_tokens":usage.total_tokens if usage.token_usage_complete else None})
    expected=[a.expected for a in case.assertions if a.evaluator=="fact"]
    text = f"# {case.case_id} Golden 审核材料\n\n状态：待人工审核，未晋升，未生成审核人。\n\n"
    text += "## 用户问题\n\n"+case.conversation[-1].content+"\n\n## 预期业务事实\n\n```json\n"
    text += json.dumps(expected,ensure_ascii=False,indent=2)+"\n```\n\n"
    text += "## 判定合同\n\n最高高度和全部并列成员必须正确；成员顺序不固定，不强制唯一工具路线。"
    text += "本轮当前证据须支持答案，缺失、重复、错日期或错高度应判错。"
    text += "运行数据/Fixture故障与Agent失败分开；额外声明单独审核，不因主问题正确而放行。\n\n"
    text += "## 原始回答（待核验材料，不是标准答案）\n\n"+response.answer+"\n\n"
    text += "## 自动诊断\n\n```json\n"+report.model_dump_json(indent=2)+"\n```\n\n"
    text += "## 审核清单\n\n- [ ] 问题、日期、研究口径与交付项合理。\n"
    text += "- [ ] 对照baseline.json独立复核标准事实及数据来源。\n"
    text += "- [ ] 可接受路线及未录制路线的处置明确。\n"
    text += "- [ ] 检查正反校准样例，不将合成审核身份当真实审核。\n\n"
    text += "通过审核仅代表题目可作为Golden，不代表本次回答所有声明正确。"
    text += "本材料尚无自动晋升权限，review.json保留待审核字段。\n"
    with (destination / "REVIEW.md").open("x",encoding="utf-8") as handle:
        handle.write(text)
    return {"case_id":case.case_id,"verdict":report.verdict,"model_calls":guarded.calls,
            "total_tokens":usage.total_tokens if usage.token_usage_complete else None,"review_status":"pending_human_review"}
