"""Execute Live Behavioral Eval cases through the production Agent runtime."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from app.agents.chat import answer_first_board_chat
from app.agents.chat_live_eval import (
    LIVE_EVAL_ENVIRONMENT_ID,
    LIVE_EVAL_VERSION,
    FailureInjection,
    LiveEvalCase,
    LiveEvalDataset,
    aggregate_live_results,
    evaluate_live_trial,
)
from app.agents.query_contract import query_reference_date_override
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage
from app.services.llm_provider import LLMProvider, capture_llm_usage
from app.services.sample_data import SAMPLE_EVENTS


DATASET_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent_chat_live_eval_v1.json"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "output" / "agent-live-eval"
RUNNER_VERSION = "agent-live-eval-runner-v1"
JUDGE_PROMPT_VERSION = "agent-live-eval-judge-v1"


def load_live_eval_dataset(path: Path = DATASET_PATH) -> LiveEvalDataset:
    return LiveEvalDataset.model_validate_json(path.read_text(encoding="utf-8"))


def run_live_eval_suite(
    cases: list[LiveEvalCase],
    *,
    llm_provider: LLMProvider,
    trials: int = 3,
    registry_factory: Callable[[LiveEvalCase], Any] | None = None,
    judge_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Run real planner/answer LLM calls and real multi-turn production orchestration."""

    if trials < 1:
        raise ValueError("trials must be positive")
    results: list[dict[str, Any]] = []
    for case in cases:
        for trial in range(1, trials + 1):
            result = run_live_eval_trial(
                case,
                trial=trial,
                llm_provider=llm_provider,
                tool_registry=(registry_factory or _default_registry)(case),
            )
            if judge_provider is not None:
                result["judge_result"] = judge_live_answer(case, result, judge_provider)
                if not result["judge_result"]["passed"]:
                    result["passed"] = False
                    result["failure_reasons"].append("LLM Judge semantic quality gate failed")
            result["trial"] = trial
            results.append(result)
    report = {
        "status": "completed",
        "run_id": f"live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
        "dataset_version": LIVE_EVAL_VERSION,
        "runner_version": RUNNER_VERSION,
        "environment_id": LIVE_EVAL_ENVIRONMENT_ID,
        "agent_architecture": "plan-and-execute",
        "observation_driven_replan_supported": False,
        "judge_enabled": judge_provider is not None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": aggregate_live_results(results),
        "results": results,
    }
    return report


def run_live_eval_trial(
    case: LiveEvalCase,
    *,
    trial: int,
    llm_provider: LLMProvider,
    tool_registry: Any,
) -> dict[str, Any]:
    """Execute every turn, feeding the actual prior answer back as session history."""

    session_id = f"live-eval-{case.case_id}-{trial}"
    history: list[ChatSessionMessage] = []
    responses = []
    started = perf_counter()
    anchor_date = datetime.fromisoformat(case.anchor_datetime).date()
    with capture_llm_usage() as usage:
        for index, turn in enumerate(case.turns, start=1):
            request = AgentChatRequest(session_id=session_id, message=turn.user)
            with query_reference_date_override(anchor_date):
                response = answer_first_board_chat(
                    request,
                    SAMPLE_EVENTS,
                    llm_provider=llm_provider,
                    conversation_messages=history,
                    tool_registry=tool_registry,
                )
            responses.append(response)
            now = datetime.now(timezone.utc)
            history.extend(
                [
                    ChatSessionMessage(
                        message_id=f"{session_id}-u{index}", session_id=session_id,
                        role="user", content=turn.user, created_at=now,
                    ),
                    ChatSessionMessage(
                        message_id=f"{session_id}-a{index}", session_id=session_id,
                        role="assistant", content=response.answer,
                        metadata={"tool_calls": response.tool_calls}, created_at=now,
                    ),
                ]
            )
    usage_payload = {
        "call_count": usage.call_count,
        "failed_call_count": usage.failed_call_count,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "complete": usage.token_usage_complete,
        "model": usage.model,
    }
    return evaluate_live_trial(
        case,
        responses,
        llm_usage=usage_payload,
        latency_ms=round((perf_counter() - started) * 1000),
    )


def judge_live_answer(case: LiveEvalCase, result: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    """Judge semantics only; all market facts are supplied from the actual trace."""

    dimensions = judge_dimensions_for_case(case)
    prompt = {
        "question": case.turns[-1].user,
        "expected_behavior": case.expected.response_behavior,
        "tool_facts": [
            {"tool": item["name"], "result": item.get("result"), "output": item.get("output")}
            for item in result["tool_trace"]
        ],
        "answer": result["answer"],
        "dimensions": dimensions,
    }
    response = provider.generate(
        "只依据给定工具事实，对输入指定的适用维度逐项评0到2分；不要评价或返回不适用维度，"
        "不要使用自身金融知识补事实。另返回rationale。Return only valid JSON.",
        json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
    )
    payload = _parse_json_object(response.content)
    scores = {name: int(payload[name]) for name in dimensions}
    values = list(scores.values())
    if any(value not in {0, 1, 2} for value in values):
        raise ValueError("Judge dimensions must be 0, 1 or 2")
    threshold = math.ceil(len(values) * 2 * 0.8)
    return {
        "dimensions": dimensions,
        "scores": scores,
        "total": sum(values),
        "passing_total": threshold,
        "passed": min(values) > 0 and sum(values) >= threshold,
        "rationale": str(payload.get("rationale") or ""),
        "model": response.model,
        "prompt_version": JUDGE_PROMPT_VERSION,
    }


def judge_dimensions_for_case(case: LiveEvalCase) -> list[str]:
    """Return only semantic dimensions applicable to this case and its tags."""

    if case.category == "boundary":
        return ["clarity", "relevance", "task_resolution", "boundary_compliance"]
    dimensions = ["completeness", "clarity", "relevance", "task_resolution"]
    if case.category == "recovery":
        dimensions.extend(("uncertainty_disclosure", "failure_transparency"))
    risk_tags = {
        "rating", "risk", "review", "dynamic_risk", "flagship", "best_pick",
    }
    if risk_tags.intersection(case.tags):
        dimensions.append("risk_explanation")
    return dimensions


class InjectedToolRegistry:
    """Eval-only proxy that injects failures without changing production orchestration."""

    def __init__(self, delegate: AgentToolRegistry, injections: list[FailureInjection]) -> None:
        self._delegate = delegate
        self._injections = injections

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._delegate, name)
        matching = [item for item in self._injections if item.tool == name]
        if not matching or not callable(value):
            return value

        def injected(*args: Any, **kwargs: Any) -> Any:
            injection = next(
                (item for item in matching if _arguments_match(item.match_args, args, kwargs)),
                None,
            )
            if injection is None:
                return value(*args, **kwargs)
            if injection.result_state == "error":
                raise RuntimeError(f"live eval injected {name} provider error")
            result = value(*args, **kwargs)
            if not isinstance(result, ToolResult):
                return result
            if injection.result_state == "empty":
                return _empty_result(result)
            return replace(
                result,
                result_status="partial",
                data_fresh=False,
                source_errors=("live_eval_injected_partial",),
            )

        return injected


def write_live_eval_report(report: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    run_dir = output_root / str(report["run_id"])
    run_dir.mkdir(parents=True, exist_ok=False)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _default_registry(case: LiveEvalCase) -> InjectedToolRegistry:
    return InjectedToolRegistry(AgentToolRegistry(events=SAMPLE_EVENTS), case.failure_injections)


def _arguments_match(expected: dict[str, Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    if not expected:
        return True
    observed = dict(kwargs)
    if args:
        observed.setdefault("symbol", args[0])
    return all(str(observed.get(key)) == str(value) for key, value in expected.items())


def _empty_result(result: ToolResult) -> ToolResult:
    output = result.output
    if isinstance(output, dict):
        output = _empty_mapping(output)
    elif isinstance(output, list):
        output = []
    elif isinstance(output, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(output).model_fields:
            value = getattr(output, name)
            if isinstance(value, list):
                updates[name] = []
            elif isinstance(value, int) and any(token in name for token in ("count", "total")):
                updates[name] = 0
        output = output.model_copy(update=updates)
    return replace(
        result,
        output=output,
        trace_output=_empty_mapping(result.trace_output),
        summary=f"{result.name} returned no rows (live eval injection).",
        result_status="empty",
        data_fresh=True,
        source_errors=(),
    )


def _empty_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    empty = dict(payload)
    for key, value in list(empty.items()):
        if isinstance(value, list):
            empty[key] = []
        elif isinstance(value, int) and any(token in key for token in ("count", "total")):
            empty[key] = 0
    return empty


def _parse_json_object(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = normalized.removeprefix("```json").removeprefix("```")
        normalized = normalized.removesuffix("```").strip()
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("Judge must return one JSON object")
    return payload
