"""Execute Live Behavioral Eval cases through the production Agent runtime."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.agents.chat import answer_first_board_chat
from app.agents.chat_eval_runner_v2 import FrozenToolFixture
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
from app.agents.tool_policy import ToolExecution
from app.agents.tools import TOOL_SCHEMAS, V1_AGENT_PROFILE, V1_CLOSED_MARKET_TOOL_NAMES
from app.models import AgentChatRequest, AgentToolOutcome, AgentToolTrace, ChatSessionMessage
from app.services.llm_provider import LLMProvider, capture_llm_usage
from app.services.sample_data import SAMPLE_EVENTS


DATASET_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent_chat_live_eval_v1.json"
LIVE_TOOL_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "agent_chat_live_tool_world_v2.json"
)
LIVE_TOOL_WORLD_ID = "chat-live-world-v2"
LIVE_TOOL_WORLD_SCHEMA_VERSION = "chat-live-tool-world-v2"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "output" / "agent-live-eval"
RUNNER_VERSION = "agent-live-eval-runner-v4"
JUDGE_PROMPT_VERSION = "agent-live-eval-judge-v2"


def load_live_eval_dataset(path: Path = DATASET_PATH) -> LiveEvalDataset:
    dataset = LiveEvalDataset.model_validate_json(path.read_text(encoding="utf-8"))
    validate_live_tool_world(dataset, load_live_tool_world())
    return dataset


def load_live_tool_world(path: Path = LIVE_TOOL_WORLD_PATH) -> FrozenToolFixture:
    return FrozenToolFixture(
        path,
        expected_snapshot_id=LIVE_TOOL_WORLD_ID,
        expected_schema_version=LIVE_TOOL_WORLD_SCHEMA_VERSION,
    )


def validate_live_tool_world(
    dataset: LiveEvalDataset,
    fixture: FrozenToolFixture,
) -> None:
    """Reject an incomplete, cross-dated or internally inconsistent Live world."""

    anchor_dates = {
        datetime.fromisoformat(case.anchor_datetime).date().isoformat()
        for case in dataset.cases
    }
    if anchor_dates != {fixture.anchor_date}:
        raise ValueError(
            f"Live case anchors {sorted(anchor_dates)} do not match {fixture.anchor_date}"
        )
    missing_tools = set(V1_CLOSED_MARKET_TOOL_NAMES) - set(fixture.tools)
    if missing_tools:
        raise ValueError(f"Live tool world is missing tools: {sorted(missing_tools)}")
    for tool_name, definition in fixture.tools.items():
        payload = definition.get("payload") or {}
        if payload.get("as_of_date") != fixture.anchor_date:
            raise ValueError(f"{tool_name} must declare as_of_date={fixture.anchor_date}")
        for item in _walk_mappings(payload):
            symbol = str(item.get("symbol") or "")
            entity = str(item.get("entity") or item.get("name") or "")
            if not symbol or not entity:
                continue
            canonical = fixture.entities.get(symbol)
            if canonical is None:
                raise ValueError(f"{tool_name} references unknown symbol {symbol}")
            if entity != canonical.get("name"):
                raise ValueError(
                    f"{tool_name} binds {symbol} to {entity}, expected {canonical.get('name')}"
                )
            sector = item.get("sector")
            if sector and sector != canonical.get("sector"):
                raise ValueError(
                    f"{tool_name} binds {symbol} to sector {sector}, "
                    f"expected {canonical.get('sector')}"
                )
    market = fixture.tools["market_summary"]["payload"]
    limit_up = fixture.tools["limit_up_events"]["payload"]["events"]
    if market.get("limit_up_count") != len(limit_up):
        raise ValueError("market_summary limit_up_count must match limit_up_events")
    if market.get("first_board_count") != sum(
        item.get("board_height") == 1 for item in limit_up
    ):
        raise ValueError("market_summary first_board_count must match limit_up_events")
    if len(fixture.tools["hot_stock_ranking"]["payload"].get("stocks", [])) < 10:
        raise ValueError("Live tool world requires at least ten hot stocks")
    if len(fixture.tools["first_board_ratings"]["payload"].get("ratings", [])) < 5:
        raise ValueError("Live tool world requires at least five rated first boards")
    rankings = fixture.tools["sector_stock_ranking"]["payload"].get("by_sector", {})
    if len(rankings.get("半导体", [])) < 5 or len(rankings.get("医药", [])) < 3:
        raise ValueError("Live tool world lacks sector comparison/ranking coverage")
    ratings = {
        item["symbol"]
        for item in fixture.tools["first_board_ratings"]["payload"]["ratings"]
    }
    hot = {
        item["symbol"]
        for item in fixture.tools["hot_stock_ranking"]["payload"]["stocks"]
    }
    limit_up_symbols = {item["symbol"] for item in limit_up}
    kline = set(fixture.tools["stock_kline"]["payload"].get("by_symbol", {}))
    news = set(fixture.tools["stock_news"]["payload"].get("by_symbol", {}))
    if "300750" not in ratings or not {"300750", "600000"} <= kline:
        raise ValueError("Live tool world lacks named-stock rating/K-line coverage")
    if "300750" not in news:
        raise ValueError("Live tool world lacks named-stock news coverage")
    if not hot & limit_up_symbols:
        raise ValueError("Live tool world must contain a non-empty hot/limit-up intersection")


def _walk_mappings(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_mappings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mappings(item)


def run_live_eval_suite(
    cases: list[LiveEvalCase],
    *,
    llm_provider: LLMProvider,
    trials: int = 3,
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
                tool_registry=_default_registry(case),
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
        "tool_environment": {
            "fixture_snapshot_id": LIVE_TOOL_WORLD_ID,
            "fully_frozen": True,
            "database_access": False,
            "network_access": False,
        },
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
        "不要使用自身金融知识补事实。只返回JSON："
        '{"scores":{"适用维度名":0},"rationale":"简要理由"}。',
        json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
    )
    payload = _parse_json_object(response.content)
    score_payload = payload.get("scores", payload)
    if not isinstance(score_payload, dict):
        raise ValueError("Judge scores must be one JSON object")
    scores = {name: int(score_payload[name]) for name in dimensions}
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


class FrozenLiveToolRegistry:
    """Eval-only registry that cannot reach production databases or providers."""

    def __init__(
        self,
        injections: list[FailureInjection],
        fixture: FrozenToolFixture | None = None,
    ) -> None:
        self.fixture = fixture or FrozenToolFixture(
            LIVE_TOOL_WORLD_PATH,
            expected_snapshot_id=LIVE_TOOL_WORLD_ID,
            expected_schema_version=LIVE_TOOL_WORLD_SCHEMA_VERSION,
        )
        self._injections = injections
        self.profile = V1_AGENT_PROFILE
        self.events = SAMPLE_EVENTS

    @property
    def enabled_tool_names(self) -> frozenset[str]:
        return V1_CLOSED_MARKET_TOOL_NAMES

    def is_enabled(self, tool_name: str) -> bool:
        return tool_name in self.enabled_tool_names

    def schemas(self) -> list[Any]:
        return [schema for schema in TOOL_SCHEMAS if self.is_enabled(schema.name)]

    def schema_prompt(self) -> str:
        return json.dumps(
            [schema.planner_dump() for schema in self.schemas()],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def resolve_stock_identity(self, value: str) -> tuple[str, str]:
        text = str(value).strip()
        symbol_match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if symbol_match:
            symbol = symbol_match.group(1)
            entity = self.fixture.entities.get(symbol, {})
            return symbol, entity.get("name", symbol)
        for symbol, entity in self.fixture.entities.items():
            name = entity.get("name", "")
            if name and name in text:
                return symbol, name
        raise ValueError(f"Cannot resolve stock symbol from frozen fixture: {value}")

    def execute_frozen_calls(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        request: AgentChatRequest,
        context_symbol: str | None = None,
    ) -> ToolExecution:
        execution: ToolExecution = {
            "facts": {},
            "tool_results": [],
            "tool_call_names": [],
            "references": [],
        }
        for call in tool_calls:
            self._execute_one(
                str(call.get("name") or ""),
                dict(call.get("arguments") or {}),
                request=request,
                execution=execution,
                context_symbol=context_symbol,
            )
        return execution

    def repair_frozen_tool(
        self,
        tool_name: str,
        *,
        request: AgentChatRequest,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        arguments: dict[str, Any] = {}
        if request.trade_date is not None:
            arguments["trade_date"] = request.trade_date.isoformat()
        try:
            symbol = self.resolve_stock_identity(
                request.symbol or request.message or context_symbol or ""
            )[0]
        except ValueError:
            symbol = context_symbol
        if symbol and tool_name in {
            "stock_news", "stock_activity", "stock_kline", "first_board_critic",
        }:
            arguments["symbol"] = symbol
        self._execute_one(
            tool_name,
            arguments,
            request=request,
            execution=execution,
            context_symbol=context_symbol,
        )

    def _execute_one(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request: AgentChatRequest,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        if not self.is_enabled(name):
            trace = AgentToolTrace(
                name=name,
                input=arguments,
                summary="冻结工具世界未启用该工具。",
                status="error",
                error="tool unavailable in frozen live eval profile",
                result=AgentToolOutcome(
                    status="error",
                    data_fresh=False,
                    source_errors=["frozen_tool_unavailable"],
                    payload={},
                ),
            )
        else:
            state = self._injected_state(name, arguments)
            trace = self._trace(name, arguments, state)
        execution["tool_results"].append(trace)
        execution["tool_call_names"].append(name)
        payload = trace.result.payload if trace.result is not None else trace.output
        if trace.result is not None and trace.result.status == "error":
            execution["facts"][f"{name}_error"] = trace.error or "frozen fixture error"
        else:
            execution["facts"][name] = payload
        if name == "first_board_ratings":
            requested = request.symbol or _symbol_from_text(request.message)
            if requested:
                candidates = payload.get("top_candidates", []) if isinstance(payload, dict) else []
                matched = next(
                    (item for item in candidates if item.get("symbol") == requested),
                    None,
                )
                execution["facts"]["first_board_rating_lookup"] = {
                    "symbol": requested,
                    "found": matched is not None,
                    "rating": matched,
                }
        if isinstance(payload, dict) and payload.get("as_of_date"):
            execution["references"].append(f"fixture_as_of={payload['as_of_date']}")

    def _trace(
        self,
        name: str,
        arguments: dict[str, Any],
        result_state: str | None,
    ) -> AgentToolTrace:
        trace = self.fixture.trace(
            name,
            arguments=arguments,
            result_state=result_state,
        )
        payload = _normalize_frozen_payload(name, deepcopy(trace.output or {}), arguments)
        result = trace.result
        if result is not None:
            result = result.model_copy(update={"payload": payload})
        return trace.model_copy(
            update={
                "name": name,
                "input": arguments,
                "output": payload,
                "summary": f"{trace.summary}（Live Eval 完全冻结）",
                "result": result,
            }
        )

    def _injected_state(self, name: str, arguments: dict[str, Any]) -> str | None:
        injection = next(
            (
                item for item in self._injections
                if item.tool == name and _mapping_matches(item.match_args, arguments)
            ),
            None,
        )
        return injection.result_state if injection is not None else None


def write_live_eval_report(report: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    run_dir = output_root / str(report["run_id"])
    run_dir.mkdir(parents=True, exist_ok=False)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _default_registry(case: LiveEvalCase) -> FrozenLiveToolRegistry:
    return FrozenLiveToolRegistry(case.failure_injections)


def _mapping_matches(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    return all(str(observed.get(key)) == str(value) for key, value in expected.items())


def _symbol_from_text(value: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", value)
    return match.group(1) if match else None


def _normalize_frozen_payload(
    tool_name: str,
    payload: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Expose production-like keys while preserving the fixture's canonical facts."""

    as_of = payload.get("as_of_date")
    if tool_name == "market_index_trend" and "indices" in payload:
        symbols = {
            "上证指数": "000001.SH",
            "深证成指": "399001.SZ",
            "创业板指": "399006.SZ",
        }
        payload["data_as_of"] = as_of
        payload["requested_days"] = int(arguments.get("days") or 5)
        payload["requested_end_date"] = as_of
        payload["data_fresh"] = True
        payload["indices"] = [
            {
                **item,
                "name": item.get("entity"),
                "symbol": symbols.get(str(item.get("entity")), "fixture-index"),
                "start_date": "2026-05-11",
                "end_date": as_of,
                "start_close": 100.0,
                "end_close": round(100.0 + float(item.get("value") or 0), 2),
                "return_pct": item.get("value"),
                "max_drawdown_pct": -0.35,
                "positive_days": 3,
                "negative_days": 2,
                "points": [
                    {"trade_date": "2026-05-11", "close": 100.0, "change_pct": None},
                    {
                        "trade_date": as_of,
                        "close": round(100.0 + float(item.get("value") or 0), 2),
                        "change_pct": item.get("value"),
                    },
                ],
                "source": LIVE_TOOL_WORLD_ID,
            }
            for item in payload["indices"]
        ]
    elif tool_name == "sector_performance" and "sectors" in payload:
        payload["data_as_of"] = as_of
        payload["top_sectors"] = [
            {
                **item,
                "sector_name": item.get("entity"),
                "change_pct": item.get("value"),
            }
            for item in payload["sectors"]
        ]
        payload["sources"] = [LIVE_TOOL_WORLD_ID]
    elif tool_name == "sector_stock_ranking":
        rankings = payload.get("by_sector", {})
        requested_sector = str(arguments.get("sector") or "半导体")
        selected = rankings.get(requested_sector)
        if selected is None and rankings:
            selected = next(iter(rankings.values()))
        payload["data_as_of"] = as_of
        payload["sector_name"] = requested_sector
        payload["stocks"] = selected or []
    elif tool_name == "hot_stock_ranking" and "stocks" in payload:
        payload["items"] = [
            {**item, "name": item.get("entity"), "rank": item.get("value")}
            for item in payload["stocks"]
        ]
        requested_count = int(arguments.get("limit") or len(payload["items"]))
        payload.update(
            {
                "source": LIVE_TOOL_WORLD_ID,
                "source_label": "冻结热股",
                "captured_at": f"{as_of}T15:10:00+08:00",
                "captured_at_beijing": f"{as_of}T15:10:00+08:00",
                "data_fresh": True,
                "requested_count": requested_count,
                "count": len(payload["items"]),
                "complete": len(payload["items"]) >= requested_count,
            }
        )
    elif tool_name == "stock_news" and "by_symbol" in payload:
        requested_symbol = str(arguments.get("symbol") or "300750")
        fetched_at = f"{as_of}T18:00:00+08:00"
        payload["symbol"] = requested_symbol
        payload["name"] = next(
            (
                str(item.get("entity"))
                for item in payload["by_symbol"].get(requested_symbol, [])
                if item.get("entity")
            ),
            requested_symbol,
        )
        payload["fetched_at"] = fetched_at
        payload["window_days"] = int(arguments.get("days") or 7)
        payload["cache_status"] = "frozen_fixture"
        payload["sources"] = [LIVE_TOOL_WORLD_ID]
        payload["data_missing"] = []
        payload["items"] = payload["by_symbol"].get(requested_symbol, [])
        payload["items"] = [
            {
                **item,
                "name": item.get("entity"),
                "title": item.get("value"),
                "summary": "冻结评测世界中的公司业务进展事实。",
                "published_at": f"{item.get('date') or as_of}T16:00:00+08:00",
                "url": f"fixture://{tool_name}/{index}",
                "item_type": "announcement_report",
                "relevance_score": 1.0,
                "fetched_at": fetched_at,
            }
            for index, item in enumerate(payload["items"], start=1)
        ]
    elif tool_name == "finance_news" and "items" in payload:
        payload["items"] = [
            {
                **item,
                "title": item.get("value"),
                "url": f"fixture://{tool_name}/{index}",
            }
            for index, item in enumerate(payload["items"], start=1)
        ]
    elif tool_name == "first_board_ratings" and "ratings" in payload:
        requested_symbols = {
            str(item) for item in arguments.get("symbols", [])
        } if isinstance(arguments.get("symbols"), list) else set()
        if requested_symbols:
            payload["ratings"] = [
                item
                for item in payload["ratings"]
                if str(item.get("symbol")) in requested_symbols
            ]
        payload["top_candidates"] = [
            {
                **item,
                "name": item.get("entity"),
                "total_score": item.get("value"),
            }
            for item in payload["ratings"]
        ]
    elif tool_name == "first_board_filter":
        payload.setdefault("matches", [])
        payload["matched_count"] = len(payload["matches"])
    elif tool_name == "stock_kline" and "by_symbol" in payload:
        raw_requested = arguments.get("symbol") or "300750"
        if isinstance(raw_requested, list):
            selected = [payload["by_symbol"][str(symbol)] for symbol in raw_requested if str(symbol) in payload["by_symbol"]]
            payload["stocks"] = selected
            payload["requested_symbols"] = [str(symbol) for symbol in raw_requested]
            payload["data_as_of"] = as_of
            return payload
        requested = str(raw_requested)
        stock = payload["by_symbol"].get(requested, {})
        payload["stock"] = stock
        payload.update(
            {
                "symbol": stock.get("symbol", requested),
                "name": stock.get("entity", requested),
                "data_as_of": as_of,
            }
        )
    elif tool_name == "stock_activity" and "by_symbol" in payload:
        requested = str(arguments.get("symbol") or "300750")
        payload["symbol"] = requested
        payload["stock"] = payload["by_symbol"].get(requested, {})
    elif tool_name == "first_board_critic" and "by_symbol" in payload:
        requested = str(arguments.get("symbol") or "301489")
        payload["symbol"] = requested
        payload["critic"] = payload["by_symbol"].get(requested, {})
    elif tool_name == "scoring_policy_status":
        policy = payload.get("policy", {})
        challenger = payload.get("challenger", {})
        payload["champion"] = {"version": policy.get("value")}
        payload["latest_optimization"] = {
            "challenger_policy": {"version": challenger.get("value")}
        }
    return payload


def _parse_json_object(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = normalized.removeprefix("```json").removeprefix("```")
        normalized = normalized.removesuffix("```").strip()
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("Judge must return one JSON object")
    return payload
