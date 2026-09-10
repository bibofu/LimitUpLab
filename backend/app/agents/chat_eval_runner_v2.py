"""Deterministic replay, live-model, shadow, Judge and artifact runner for Chat Eval V2."""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable
from uuid import uuid4

from app.agents.chat import plan_agent_query
from app.agents.chat_eval_dataset import (
    CHAT_EVAL_DATASET_VERSION,
    CHAT_EVAL_FIXTURE_ID,
    ChatEvalCase,
)
from app.agents.chat_eval_v2 import (
    ChatEvalTrialResult,
    EvalMode,
    JudgeScores,
    build_suite_report,
    evaluate_chat_response,
)
from app.agents.query_contract import (
    build_conversation_query_understanding_view,
    query_reference_date_override,
)
from app.models import (
    AgentChatPerformance,
    AgentChatRequest,
    AgentChatResponse,
    AgentRun,
    AgentToolOutcome,
    AgentToolPolicyAudit,
    AgentToolTrace,
    ChatSessionMessage,
)
from app.repositories import SQLiteAgentRunRepository
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


RUNNER_VERSION = "chat-eval-runner-v2"
JUDGE_PROMPT_VERSION = "chat-eval-judge-v1"
TOOL_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "agent_chat_eval_tool_fixture_v2.json"
)
DEFAULT_REPORT_ROOT = Path(__file__).resolve().parents[3] / "output" / "agent-eval"
INTERNAL_TRACE_NAMES = {
    "agent_plan",
    "query_understanding",
    "llm_tool_planner",
    "llm_tool_answer",
    "template_general_answer",
    "tool_policy",
}


class EvalConfigurationError(RuntimeError):
    """Raised before execution when a requested eval dependency is unavailable."""


class FrozenToolFixture:
    """Read-only versioned facts used by both offline replay and live-model eval."""

    def __init__(self, path: Path = TOOL_FIXTURE_PATH) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("fixture_snapshot_id") != CHAT_EVAL_FIXTURE_ID:
            raise ValueError("tool fixture snapshot does not match the dataset")
        if payload.get("schema_version") != "chat-eval-tool-fixture-v2":
            raise ValueError("unsupported Chat Eval tool fixture schema")
        if not isinstance(payload.get("tools"), dict):
            raise ValueError("tool fixture must contain a tools object")
        self.path = path
        self.anchor_date = str(payload.get("anchor_date") or "")
        self.tools: dict[str, dict[str, Any]] = payload["tools"]

    def trace(
        self,
        tool: str,
        *,
        arguments: dict[str, Any],
        result_state: str,
    ) -> AgentToolTrace:
        fixture = self.tools.get(tool)
        if fixture is None:
            return AgentToolTrace(
                name=tool,
                input=arguments,
                summary="冻结 fixture 未定义该工具。",
                status="error",
                error="missing frozen tool fixture",
                result=AgentToolOutcome(
                    status="error",
                    data_fresh=False,
                    source_errors=["missing frozen tool fixture"],
                    payload={},
                ),
            )
        state = result_state if result_state in {"ok", "empty", "partial", "error"} else "ok"
        payload = dict(fixture.get("payload") or {})
        source_errors: list[str] = []
        if state == "empty":
            payload = {"as_of_date": payload.get("as_of_date"), "items": []}
        elif state == "partial":
            source_errors = ["fixture_secondary_source_unavailable"]
            payload["data_missing"] = ["secondary_source"]
        elif state == "error":
            payload = {}
            source_errors = ["fixture_provider_error"]
        return AgentToolTrace(
            name=tool,
            input=arguments,
            summary=str(fixture.get("summary") or f"冻结 {tool} 结果"),
            status="error" if state == "error" else "success",
            error="fixture_provider_error" if state == "error" else None,
            duration_ms=0,
            output=payload,
            result=AgentToolOutcome(
                status=state,  # type: ignore[arg-type]
                data_fresh=state in {"ok", "partial"},
                source_errors=source_errors,
                payload=payload,
            ),
        )


def select_eval_cases(
    cases: Iterable[ChatEvalCase],
    *,
    sample_size: int | None,
    seed: str,
) -> list[ChatEvalCase]:
    """Select a repeatable type-stratified sample without changing full-run order."""

    available = list(cases)
    if sample_size is None or sample_size >= len(available):
        return available
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    rng = random.Random(seed)
    strata: dict[str, list[ChatEvalCase]] = defaultdict(list)
    for case in available:
        strata[case.primary_type].append(case)
    for items in strata.values():
        rng.shuffle(items)
    selected: list[ChatEvalCase] = []
    keys = sorted(strata)
    while len(selected) < sample_size:
        progressed = False
        for key in keys:
            if strata[key] and len(selected) < sample_size:
                selected.append(strata[key].pop())
                progressed = True
        if not progressed:
            break
    return selected


def run_chat_eval_suite(
    cases: Iterable[ChatEvalCase],
    *,
    mode: EvalMode,
    trials: int,
    seed: str,
    sample_size: int | None = None,
    llm_provider: LLMProvider | None = None,
    judge_provider: LLMProvider | None = None,
    fixture: FrozenToolFixture | None = None,
) -> dict[str, Any]:
    """Run an immutable dataset through replay/live evaluation and aggregate it."""

    if mode == "online-shadow":
        raise ValueError("use run_online_shadow_eval for persisted production traces")
    if trials < 1:
        raise ValueError("trials must be positive")
    if mode == "live" and llm_provider is None:
        raise EvalConfigurationError("live mode requires a configured LLM provider")
    selected = select_eval_cases(cases, sample_size=sample_size, seed=seed)
    if not selected:
        raise ValueError("no Chat Eval cases selected")
    frozen = fixture or FrozenToolFixture()
    results: list[ChatEvalTrialResult] = []
    for case in selected:
        for trial in range(1, trials + 1):
            response, usage, provider_failed = _run_case(
                case,
                mode=mode,
                llm_provider=llm_provider,
                fixture=frozen,
                trial=trial,
            )
            judge = (
                judge_answer(case, response, judge_provider)
                if judge_provider is not None
                else None
            )
            results.append(
                evaluate_chat_response(
                    case,
                    response,
                    mode=mode,
                    trial=trial,
                    judge=judge,
                    provider_failed=provider_failed,
                    usage=usage,
                )
            )
    run_id = _new_run_id(mode)
    report = build_suite_report(
        results,
        mode=mode,
        dataset_version=CHAT_EVAL_DATASET_VERSION,
        run_id=run_id,
        judge_enabled=judge_provider is not None,
    )
    report.update(
        {
            "status": "completed",
            "runner_version": RUNNER_VERSION,
            "fixture_snapshot_id": CHAT_EVAL_FIXTURE_ID,
            "seed": seed,
            "sample_size": len(selected),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return report


def run_online_shadow_eval(
    *,
    sample_size: int,
    seed: str,
    repository: SQLiteAgentRunRepository | None = None,
) -> dict[str, Any]:
    """Evaluate recent persisted responses without rerunning or changing user answers."""

    if not 1 <= sample_size <= 50:
        raise ValueError("online-shadow sample_size must be between 1 and 50")
    runs = (repository or SQLiteAgentRunRepository()).list_runs(limit=500)
    candidates = [
        run
        for run in runs
        if run.run_type == "agent_chat"
        and run.status == "success"
        and run.output_json is not None
        and str(run.input_json.get("message") or "").strip()
    ]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    selected = candidates[:sample_size]
    if not selected:
        raise ValueError("no persisted Agent chat traces are available for shadow eval")
    results = [_evaluate_shadow_run(run, index + 1) for index, run in enumerate(selected)]
    report = build_suite_report(
        results,
        mode="online-shadow",
        dataset_version="anonymous-production-traces-v1",
        run_id=_new_run_id("online-shadow"),
        judge_enabled=False,
    )
    report.update(
        {
            "status": "completed",
            "runner_version": RUNNER_VERSION,
            "fixture_snapshot_id": None,
            "seed": seed,
            "sample_size": len(selected),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "privacy": "questions are not written to the report",
        }
    )
    return report


def write_completed_report(
    report: dict[str, Any],
    *,
    output_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Atomically publish one completed report and its compact latest pointer."""

    if report.get("status") != "completed" or not report.get("run_id"):
        raise ValueError("only completed reports may be published")
    run_dir = output_root / str(report["run_id"])
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "summary.json"
    failure_path = run_dir / "failures.json"
    _write_json(summary_path, report)
    failure_report = {
        key: value for key, value in report.items() if key != "results"
    }
    failure_report["results"] = [
        item for item in report.get("results", []) if not item.get("passed")
    ]
    _write_json(failure_path, failure_report)
    _write_json(output_root / "latest.json", report)
    return summary_path


def load_latest_completed_report(
    output_root: Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Any]:
    """Read the last successfully published report without executing evaluation."""

    path = output_root / "latest.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "completed":
        raise ValueError("latest Agent eval report is not complete")
    return report


def judge_answer(
    case: ChatEvalCase,
    response: AgentChatResponse,
    provider: LLMProvider | None,
) -> JudgeScores:
    """Score language quality only; market truth is supplied exclusively by traces."""

    if provider is None:
        raise EvalConfigurationError("--judge requires an independent judge provider")
    evidence = [
        {
            "tool": trace.name,
            "result_state": trace.result.status if trace.result else None,
            "payload": trace.result.payload if trace.result else trace.output,
        }
        for trace in response.tool_results
        if trace.name not in INTERNAL_TRACE_NAMES
    ]
    system_prompt = (
        "你是固定版本的回答质量裁判。只评价相关性、完整性、解释质量、不确定性表达和简洁性，"
        "每项只能为0、1、2。市场数字只能依据给定工具事实，不得使用自身知识纠正或补充。"
        "只输出JSON对象，字段为 relevance, completeness, explanation, uncertainty, concision, rationale。"
    )
    user_prompt = json.dumps(
        {
            "prompt_version": JUDGE_PROMPT_VERSION,
            "question": case.conversation[-1].content,
            "expected_behavior": case.expected.response_behavior,
            "tool_facts": evidence,
            "answer": response.answer,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    result = provider.generate(system_prompt, user_prompt)
    payload = _parse_json_object(result.content)
    return JudgeScores(
        relevance=int(payload["relevance"]),
        completeness=int(payload["completeness"]),
        explanation=int(payload["explanation"]),
        uncertainty=int(payload["uncertainty"]),
        concision=int(payload["concision"]),
        rationale=str(payload.get("rationale") or ""),
        model=result.model,
        prompt_version=JUDGE_PROMPT_VERSION,
    )


def _run_case(
    case: ChatEvalCase,
    *,
    mode: EvalMode,
    llm_provider: LLMProvider | None,
    fixture: FrozenToolFixture,
    trial: int,
) -> tuple[AgentChatResponse, dict[str, Any], bool]:
    started_at = perf_counter()
    anchor_date = case.anchor_datetime.date()
    message = case.conversation[-1].content
    query_trace = _query_trace(case)
    if mode == "offline":
        final_tools = list(case.expected.required_tools)
        traces = [
            _offline_planner_trace(case),
            *[
                fixture.trace(
                    tool,
                    arguments=case.expected.tool_parameters.get(tool, {}),
                    result_state=case.expected.result_states.get(tool, "ok"),
                )
                for tool in final_tools
            ],
            query_trace,
        ]
        answer = _template_answer(case, traces)
        return (
            _response(
                case,
                answer=answer,
                traces=traces,
                planner_tools=final_tools,
                final_tools=final_tools,
                total_ms=round((perf_counter() - started_at) * 1000),
            ),
            {},
            False,
        )

    assert llm_provider is not None
    planner_result: LLMResult | None = None
    answer_result: LLMResult | None = None
    provider_failed = False
    planner_trace: AgentToolTrace
    planner_tools: list[str] = []
    final_tools: list[str] = []
    try:
        with query_reference_date_override(anchor_date):
            plan = plan_agent_query(
                AgentChatRequest(
                    session_id=f"eval-{case.case_id}-{trial}",
                    message=message,
                ),
                SAMPLE_EVENTS,
                llm_provider,
                conversation_messages=_conversation_messages(case, trial),
            )
        planner_result = plan.result
        planner_tools = [
            str(call.get("name"))
            for call in plan.payload.get("raw_tool_calls", [])
            if call.get("name")
        ]
        final_calls = [
            call for call in plan.tool_calls if str(call.get("name") or "") in fixture.tools
        ]
        final_tools = [str(call["name"]) for call in final_calls]
        planner_trace = AgentToolTrace(
            name="llm_tool_planner",
            input={
                "model": plan.result.model,
                "provider": plan.result.provider,
                "capabilities": list(plan.policy_capabilities),
                "resolved_capabilities": list(plan.capabilities),
                "tool_calls": plan.payload.get("raw_tool_calls", []),
                "resolved_tool_calls": plan.tool_calls,
                "planner_mode": plan.payload.get("planner_mode"),
            },
            summary="真实模型原始规划及确定性解析结果。",
            duration_ms=plan.duration_ms,
        )
        traces = [planner_trace]
        for call in final_calls:
            tool = str(call["name"])
            traces.append(
                fixture.trace(
                    tool,
                    arguments=dict(call.get("arguments") or {}),
                    result_state=case.expected.result_states.get(tool, "ok"),
                )
            )
        traces.append(query_trace)
        answer_result = _generate_live_answer(case, traces, llm_provider)
        answer = answer_result.content
    except Exception as error:
        provider_failed = True
        traces = [
            AgentToolTrace(
                name="llm_tool_planner",
                input={"capabilities": [], "tool_calls": []},
                summary="模型调用失败。",
                status="error",
                error=f"{type(error).__name__}: {error}",
            ),
            query_trace,
        ]
        answer = "模型服务暂时不可用，无法基于冻结事实完成本次回答。"
    usage = _sum_usage(planner_result, answer_result)
    response = _response(
        case,
        answer=answer,
        traces=traces,
        planner_tools=planner_tools,
        final_tools=final_tools,
        total_ms=round((perf_counter() - started_at) * 1000),
        planner_ms=planner_result.duration_ms if planner_result else 0,
        answer_ms=answer_result.duration_ms if answer_result else 0,
    )
    return response, usage, provider_failed


def _response(
    case: ChatEvalCase,
    *,
    answer: str,
    traces: list[AgentToolTrace],
    planner_tools: list[str],
    final_tools: list[str],
    total_ms: int,
    planner_ms: int = 0,
    answer_ms: int = 0,
) -> AgentChatResponse:
    repairs = [tool for tool in final_tools if tool not in planner_tools]
    audit_final_tools = final_tools or ["llm_tool_planner"]
    return AgentChatResponse(
        session_id=f"eval-{case.case_id}",
        intent="chat_eval_v2",
        answer=answer,
        tool_calls=["llm_tool_planner", *final_tools],
        tool_results=traces,
        tool_policy=AgentToolPolicyAudit(
            planner_tool_calls=planner_tools,
            final_tool_calls=audit_final_tools,
            backend_repaired_tools=repairs if case.expected.policy_repairs.repair_needed else [],
            repair_reasons=["required frozen evidence"] if repairs else [],
        ),
        performance=AgentChatPerformance(
            planner_duration_ms=planner_ms,
            answer_duration_ms=answer_ms,
            total_duration_ms=total_ms,
        ),
        generated_by=RUNNER_VERSION,
    )


def _query_trace(case: ChatEvalCase) -> AgentToolTrace:
    with query_reference_date_override(case.anchor_datetime.date()):
        view = build_conversation_query_understanding_view(
            [turn.content for turn in case.conversation if turn.role == "user"]
        )
    return AgentToolTrace(
        name="query_understanding",
        input=view,
        output=view,
        summary="基于 case anchor_datetime 的确定性 Query View。",
    )


def _offline_planner_trace(case: ChatEvalCase) -> AgentToolTrace:
    capabilities = case.expected.allowed_capability_sets[0]
    calls = [
        {"name": tool, "arguments": case.expected.tool_parameters.get(tool, {})}
        for tool in case.expected.required_tools
    ]
    return AgentToolTrace(
        name="llm_tool_planner",
        input={
            "model": "deterministic-fixture",
            "provider": "fixture",
            "capabilities": capabilities,
            "resolved_capabilities": capabilities,
            "tool_calls": calls,
            "resolved_tool_calls": calls,
            "planner_mode": "offline_fixture",
        },
        summary="离线确定性 Planner fixture；不计入模型准确率。",
    )


def _template_answer(case: ChatEvalCase, traces: list[AgentToolTrace]) -> str:
    behavior = case.expected.response_behavior
    if behavior == "refuse":
        return "我不能按要求提供确定性投资指令；该请求也不属于可核验的收盘研究范围。"
    if behavior == "clarify":
        return "请补充具体股票、板块、日期或研究范围，我再基于结构化事实回答。"
    if behavior == "empty_disclosure":
        states = [
            trace.result.status
            for trace in traces
            if trace.result is not None and trace.name not in INTERNAL_TRACE_NAMES
        ]
        if "partial" in states:
            facts = "；".join(
                _render_expected_claim(claim)
                for claim in case.expected.evidence_claims
            )
            prefix = f"已核验的部分事实为：{facts}；" if facts else ""
            return f"{prefix}冻结工具只返回部分数据；缺失来源已披露，因此不补造未返回的事实。"
        return "冻结工具暂无可用数据或执行失败，因此无法给出事实结论。"
    facts = [_render_expected_claim(claim) for claim in case.expected.evidence_claims]
    summaries = [
        trace.summary
        for trace in traces
        if trace.name not in INTERNAL_TRACE_NAMES and trace.result is not None
    ]
    core = "；".join(facts or summaries[:4]) or "冻结结构化事实已核验"
    assertions = case.expected.answer_assertions
    required = "；".join(assertions.must_include + assertions.required_symbols)
    suffix = f"；{required}" if required else ""
    return f"{core}{suffix}。仅作收盘后研究，不构成投资建议。"


def _render_expected_claim(claim: Any) -> str:
    entity = claim.entity or "样本"
    claim_date = f"在{claim.date}" if claim.date else ""
    metric = claim.metric
    value = claim.value
    suffix = ""
    if isinstance(value, (int, float)):
        if "pct" in metric:
            suffix = "%"
        elif "score" in metric:
            suffix = "分"
        elif "rank" in metric:
            suffix = "名"
        elif "board" in metric:
            suffix = "板"
        elif any(term in metric for term in ("count", "size", "rows")):
            suffix = "个"
        elif "buy" in metric:
            suffix = "元"
    return f"{entity}{claim_date}的{metric}为{value}{suffix}"


def _generate_live_answer(
    case: ChatEvalCase,
    traces: list[AgentToolTrace],
    provider: LLMProvider,
) -> LLMResult:
    evidence = [
        {
            "tool": trace.name,
            "result_state": trace.result.status if trace.result else None,
            "summary": trace.summary,
            "payload": trace.result.payload if trace.result else trace.output,
        }
        for trace in traces
        if trace.name not in INTERNAL_TRACE_NAMES
    ]
    system_prompt = (
        "你是 LimitUpLab 收盘研究助手。只能依据给定冻结工具事实回答；错误、空结果和缺失必须"
        "明确披露，不得使用模型记忆补数字。不得给出买卖、仓位、目标价、收益承诺或确定性预测。"
        "回答使用简洁中文。"
    )
    user_prompt = json.dumps(
        {
            "conversation": [turn.model_dump() for turn in case.conversation],
            "anchor_datetime": case.anchor_datetime.isoformat(),
            "tool_facts": evidence,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return provider.generate(system_prompt, user_prompt)


def _conversation_messages(
    case: ChatEvalCase, trial: int
) -> list[ChatSessionMessage]:
    return [
        ChatSessionMessage(
            message_id=f"eval-{case.case_id}-{trial}-{index}",
            session_id=f"eval-{case.case_id}-{trial}",
            role=turn.role,
            content=turn.content,
            created_at=case.anchor_datetime,
        )
        for index, turn in enumerate(case.conversation[:-1])
    ]


def _evaluate_shadow_run(run: AgentRun, trial: int) -> ChatEvalTrialResult:
    response = AgentChatResponse.model_validate(run.output_json)
    case = ChatEvalCase.model_validate(
        {
            "case_id": f"CEV2-D{trial:03d}",
            "dataset": "dev",
            "profile": "v1_close_review",
            "severity": "normal",
            "primary_type": "out_of_scope",
            "tags": ["online_shadow", f"run_hash_{abs(hash(run.run_id)) % 100000:05d}"],
            "fixture_snapshot_id": CHAT_EVAL_FIXTURE_ID,
            "anchor_datetime": run.started_at.isoformat(),
            "conversation": [{"role": "user", "content": str(run.input_json["message"])}],
            "expected": {
                "allowed_capability_sets": [[]],
                "response_behavior": "answer",
            },
        }
    )
    return evaluate_chat_response(
        case,
        response,
        mode="online-shadow",
        trial=trial,
        provider_failed=False,
    )


def _sum_usage(*results: LLMResult | None) -> dict[str, Any]:
    values = [item for item in results if item is not None]
    complete = values and all(item.total_tokens is not None for item in values)
    return {
        "prompt_tokens": sum(item.prompt_tokens or 0 for item in values) if complete else None,
        "completion_tokens": sum(item.completion_tokens or 0 for item in values) if complete else None,
        "total_tokens": sum(item.total_tokens or 0 for item in values) if complete else None,
    }


def _parse_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Judge output must be a JSON object")
    return payload


def _new_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"cev2-{mode}-{stamp}-{uuid4().hex[:8]}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
