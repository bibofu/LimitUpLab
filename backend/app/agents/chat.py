"""Tool-grounded first-board chat agent."""

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from time import perf_counter
from typing import Any, Callable, Iterator

from app.agents.limit_up_execution import execute_limit_up_query
from app.agents.tool_execution import execute_tool_calls as _execute_llm_tool_calls
from app.agents.tool_execution.helpers import (
    _FirstBoardFilterQuery,
    _build_first_board_filter_trace,
    _compact_ratings_facts,
    _extract_first_board_filter,
    _extract_symbol_hint,
    _filter_first_board_candidates,
    _filter_query_from_context,
    _has_events_for_date,
    _parse_optional_date,
    _rating_fact,
    _summarize_first_board_industries,
    _tool_error_trace,
)
from app.agents.explanation import explain_first_board_rating
from app.agents.chat_answer_validation import (
    _add_composed_tool_facts,
    _contains_complete_position_groups,
    _contains_daily_promotion_facts,
    _contains_every_event_symbol,
    _contains_every_hot_stock_symbol,
    _contains_exact_hot_stock_event_intersection,
    _contains_high_score_promotion_facts,
    _requires_complete_hot_stock_answer,
    _requires_complete_position_answer,
    _requires_daily_promotion_answer,
    _requires_exhaustive_event_answer,
    _requires_high_score_promotion_answer,
    _requires_hot_stock_event_intersection_answer,
)
from app.agents.chat_plan_normalization import (
    _normalize_broad_sector_plan,
    _normalize_daily_board_promotion_tool_calls,
    _normalize_explicit_stock_evidence_plan,
    _normalize_explicit_stock_tool_calls,
    _normalize_first_board_position_tool_calls,
    _normalize_market_event_plan,
    _normalize_tool_calls,
    _parse_json_object,
)
from app.agents.chat_prompts import (
    PLANNER_CONTRACT_VERSION,
    PLANNER_FUNCTION_DESCRIPTION,
    PLANNER_FUNCTION_NAME,
    _planner_function_parameters,
    _tool_answer_system_prompt,
    _tool_answer_user_prompt,
    _tool_planner_system_prompt,
    _tool_planner_user_prompt,
    planner_prompt_component_sizes,
)
from app.agents.chat_templates import (
    TEXT,
    UNANSWERABLE_TEXT,
    _template_answer_from_tool_facts,
    _template_daily_board_promotion_answer,
    _template_first_board_position_answer,
)
from app.agents.capability_contract import (
    capability_answer_instruction,
    capability_schema_prompt,
    ensure_capability_tool_calls,
    infer_capabilities_from_facts,
    normalize_capabilities,
)
from app.agent_output_sanitizer import (
    AgentAnswerStreamSanitizer,
    friendly_tool_label,
)
from app.agents.query_contract import (
    MARKET_SEGMENT_LABELS,
    build_conversation_query_understanding_view,
    build_market_event_query_contract,
    build_limit_up_query_contract,
    current_query_reference_date,
    extract_market_event_type,
    looks_like_named_limit_up_sector_list_question,
    looks_like_limit_up_sector_summary_question,
    looks_like_market_event_query,
)
from app.post_limit_query_contract import (
    build_post_limit_query_contract,
    looks_like_post_limit_question,
)
from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals as _QuestionSignals,
    ToolExecution,
    extract_market_index_days as _extract_market_index_days,
    extract_kline_days as _extract_kline_days,
    extract_promotion_days as _extract_promotion_days,
    extract_trade_date as _extract_trade_date,
    looks_like_broad_sector_ranking_question as _looks_like_broad_sector_ranking_question,
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
    looks_like_promotion_opening_question as _looks_like_promotion_opening_question,
    looks_like_rating_explain_question as _looks_like_rating_explain_question,
    looks_like_stock_kline_question as _looks_like_stock_kline_question,
)
from app.agents.tools import (
    AgentToolRegistry,
    EXTENDED_AGENT_PROFILE,
    ToolResult,
    compact_prediction_quality_audit,
)
from app.models import (
    AgentChatRequest,
    AgentChatPerformance,
    AgentChatResponse,
    AgentToolTrace,
    AgentRun,
    ChatSessionMemory,
    ChatSessionMessage,
    build_agent_evidence_cards,
    build_agent_tool_policy_audit,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketIndexTrendFacts,
    MarketSummary,
    SectorPerformanceFacts,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import (
    LLMProvider,
    LLMResult,
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    get_llm_provider,
)
from app.services.prompt_security import (
    assess_direct_prompt_injection,
    contains_prompt_leak,
)
from app.services.session_memory import memory_prompt_payload


CHAT_AGENT_VERSION = "first-board-chat-langgraph-phase2-v20"
_FORCE_TEMPLATE_ANSWER_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "force_template_answer_override",
    default=None,
)
SEMI = "\uff1b"
IDEOGRAPHIC_COMMA = "\u3001"
PLANNER_DIRECT_ANSWER_INTENTS = {"capability_intro", "greeting", "smalltalk"}
SUPPORTED_INTENTS = {
    "capability_intro",
    "greeting",
    "smalltalk",
    "prompt_injection_refusal",
    "out_of_scope",
    "unsafe_investment_advice",
    "market_schedule",
    "market_context",
    "general_llm",
    "tool_grounded_answer",
    "stock_trend",
    "risk_summary",
    "rating_explain",
    "first_board_filter",
    "first_board_context_top",
    "first_board_sector_summary",
    "market_event_query",
    "limit_up_query",
    "today_summary",
    "sector_performance",
    "llm_explanation",
}


@contextmanager
def template_answer_override(enabled: bool) -> Iterator[None]:
    """Override template-answer mode inside the current request or eval context."""

    token = _FORCE_TEMPLATE_ANSWER_OVERRIDE.set(enabled)
    try:
        yield
    finally:
        _FORCE_TEMPLATE_ANSWER_OVERRIDE.reset(token)




KEYWORDS = {
    "capability_intro": ("\u4f60\u80fd\u505a\u4ec0\u4e48", "\u4f60\u4f1a\u4ec0\u4e48", "\u600e\u4e48\u7528", "\u80fd\u529b", "\u529f\u80fd", "\u5e2e\u52a9", "help"),
    "greeting": ("\u4f60\u597d", "\u55e8", "hello", "hi"),
    "smalltalk": ("\u8c22\u8c22", "\u597d\u7684", "\u7ee7\u7eed", "ok", "thanks"),
    "market_schedule": ("\u5f00\u76d8", "\u6536\u76d8", "\u96c6\u5408\u7ade\u4ef7", "\u4ea4\u6613\u65f6\u95f4", "open", "close"),
    "market_context": ("\u5e02\u573a", "\u60c5\u7eea", "\u8d5a\u94b1\u6548\u5e94", "\u4e8f\u94b1\u6548\u5e94", "\u6c1b\u56f4", "sentiment", "market"),
    "risk_summary": ("\u98ce\u9669", "\u7f3a\u70b9", "\u95ee\u9898", "risk"),
    "llm_explanation": ("\u8be6\u7ec6", "\u89e3\u91ca", "\u5206\u6790", "explain"),
    "rating_explain": ("\u4e3a\u4ec0\u4e48", "\u8bc4\u5206", "\u8bc4\u7ea7", "\u9ad8\u5206", "\u4f4e\u5206", "score"),
    "first_board_filter": ("\u76f8\u5173", "\u884c\u4e1a", "\u9898\u6750", "\u533b\u836f", "\u533b\u7597", "\u5236\u836f", "\u836f\u4e1a", "\u751f\u7269"),
    "first_board_sector_summary": ("\u677f\u5757", "\u884c\u4e1a", "\u4e3b\u8981\u677f\u5757", "\u54ea\u4e9b\u677f\u5757"),
    "limit_up_query": ("\u6da8\u505c", "\u8fde\u677f", "\u4e8c\u8fde\u677f", "\u4e09\u8fde\u677f", "\u6700\u9ad8\u677f", "\u68af\u961f", "\u70b8\u677f"),
    "today_summary": ("\u603b\u7ed3", "\u4eca\u5929", "\u9996\u677f", "\u5019\u9009", "summary"),
}






class AgentQueryPlan:
    """Normalized LLM plan shared by runtime execution and semantic evals."""

    # Initialize AgentQueryPlan with the supplied dependencies and per-instance state.
    def __init__(
        self,
        *,
        payload: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        capabilities: tuple[str, ...],
        policy_capabilities: tuple[str, ...],
        context_mode: str,
        direct_answer: str,
        result: Any,
        duration_ms: int,
        prompt_chars: int,
    ) -> None:
        self.payload = payload
        self.tool_calls = tool_calls
        self.capabilities = capabilities
        self.policy_capabilities = policy_capabilities
        self.context_mode = context_mode
        self.direct_answer = direct_answer
        self.result = result
        self.duration_ms = duration_ms
        self.prompt_chars = prompt_chars


def plan_agent_query(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    *,
    conversation_messages: list[ChatSessionMessage] | None = None,
    session_memory: ChatSessionMemory | None = None,
    repository: SQLiteFirstBoardRepository | None = None,
) -> AgentQueryPlan:
    """Run only the production LLM planning stage without executing evidence tools."""

    tools = AgentToolRegistry(
        events=events,
        first_board_repository=repository or SQLiteFirstBoardRepository(),
    )
    context = _build_session_context(
        [],
        conversation_messages or [],
        session_memory=session_memory,
    )
    return _generate_llm_query_plan(request, tools, context, llm_provider)


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository | None = None,
    recent_runs: list[AgentRun] | None = None,
    conversation_messages: list[ChatSessionMessage] | None = None,
    session_memory: ChatSessionMemory | None = None,
    llm_provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
    tool_registry: AgentToolRegistry | None = None,
) -> AgentChatResponse:
    """Execute the single ReAct runtime for every chat request."""
    from app.agents.react_runtime.runtime import run
    response = run(
        request, tool_registry or AgentToolRegistry(
            events=events, first_board_repository=repository or SQLiteFirstBoardRepository(),
        ), llm_provider or get_llm_provider(), conversation_messages, session_memory, progress_callback,
    )
    if answer_delta_callback:
        answer_delta_callback(response.answer)
    return response










def _generate_llm_query_plan(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    context: "_SessionContext",
    provider: LLMProvider,
) -> AgentQueryPlan:
    """Generate and normalize the production LLM query plan."""

    # The native schema asks for capabilities and conversational intent rather
    # than arbitrary executable code. The backend maps those capabilities to its
    # registered tools and derives domain arguments from the request/contracts.

    injection = assess_direct_prompt_injection(request.message)
    if injection.detected:
        payload = {
            "intent_label": "prompt_injection_refusal",
            "capabilities": [],
            "context_mode": "standalone",
            "context_capabilities": [],
            "safety": "normal",
            "tool_calls": [],
            "planner_mode": "deterministic_prompt_security",
            "prompt_security_signals": list(injection.signals),
        }
        return AgentQueryPlan(
            payload=payload,
            tool_calls=[],
            capabilities=(),
            policy_capabilities=(),
            context_mode="standalone",
            direct_answer="",
            result=LLMResult(
                content="",
                model="deterministic-prompt-security",
                provider="server",
                response_mode="deterministic",
            ),
            duration_ms=0,
            prompt_chars=0,
        )

    native_system_prompt = _tool_planner_system_prompt(
        capability_schema_prompt(tools.enabled_tool_names),
        tools.profile,
        output_mode="function_call",
    )
    planner_user_prompt = _tool_planner_user_prompt(request, context, tools.events)
    started_at = perf_counter()
    planner_mode = "native_function_call"
    native_error: str | None = None
    used_system_prompt = native_system_prompt
    try:
        result = provider.generate_function_call(
            native_system_prompt,
            planner_user_prompt,
            function_name=PLANNER_FUNCTION_NAME,
            function_description=PLANNER_FUNCTION_DESCRIPTION,
            parameters=_planner_function_parameters(tools),
        )
    except (
        NativeFunctionCallingUnavailable,
        NativeFunctionCallingError,
        RuntimeError,
    ) as error:
        planner_mode = "prompt_json_fallback"
        native_error = type(error).__name__
        fallback_system_prompt = _tool_planner_system_prompt(
            capability_schema_prompt(tools.enabled_tool_names),
            tools.profile,
            output_mode="json",
        )
        used_system_prompt = fallback_system_prompt
        result = provider.generate(fallback_system_prompt, planner_user_prompt)
    payload = _parse_json_object(result.content)
    duration_ms = result.duration_ms or round((perf_counter() - started_at) * 1000)
    prompt_chars = result.prompt_chars or (
        len(used_system_prompt) + len(planner_user_prompt)
    )
    payload["planner_mode"] = planner_mode
    payload["planner_contract_version"] = PLANNER_CONTRACT_VERSION
    payload["prompt_components"] = {
        **planner_prompt_component_sizes(tools),
        "planner_user_chars": len(planner_user_prompt),
    }
    if native_error:
        payload["native_function_call_fallback_reason"] = native_error

    raw_planner_capabilities = (
        list(payload.get("capabilities"))
        if isinstance(payload.get("capabilities"), list)
        else []
    )
    raw_planner_tool_calls = _normalize_tool_calls(payload.get("tool_calls"))
    tool_calls = list(raw_planner_tool_calls)
    tool_calls = _normalize_first_board_position_tool_calls(request, tool_calls)
    tool_calls = _normalize_daily_board_promotion_tool_calls(
        request,
        tool_calls,
        default_days=context.promotion_days or 5,
    )
    is_post_limit_query = looks_like_post_limit_question(
        request.message
    ) or _looks_like_post_limit_context_followup(request.message, context)
    if is_post_limit_query:
        planner_post_arguments = next(
            (
                call.get("arguments") or {}
                for call in tool_calls
                if call.get("name") in {
                    "post_limit_screen", "post_limit_path", "post_limit_statistics"
                }
            ),
            {},
        )
        if context.anchor_date and not planner_post_arguments.get("anchor_date"):
            planner_post_arguments["anchor_date"] = context.anchor_date.isoformat()
        if _looks_like_post_limit_context_followup(request.message, context):
            planner_post_arguments["mode"] = "path"
        post_contract = build_post_limit_query_contract(
            request.message,
            request_trade_date=request.trade_date,
            planner_arguments=planner_post_arguments,
        )
        required_post_tool = {
            "screen": "post_limit_screen",
            "path": "post_limit_path",
            "statistics": "post_limit_statistics",
        }[post_contract.mode]
        tool_calls = [{
            "name": required_post_tool,
            "arguments": post_contract.to_tool_arguments(),
        }]
    # Planner output is never executable user-facing text. Conversational intents
    # are rendered from server-owned templates after the plan is normalized.
    # A planner response is control data, never a trusted final answer. Remove
    # legacy direct-answer text even if a provider still returns that field.
    payload.pop("answer_directly", None)
    direct_answer = ""
    context_mode = str(payload.get("context_mode") or "standalone").strip().lower()
    if context_mode not in {"standalone", "entity_followup", "source_refinement"}:
        context_mode = "standalone"
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list):
        raw_capabilities = []
    if is_post_limit_query:
        raw_capabilities = [{
            "screen": "post_limit_screening",
            "path": "post_limit_path",
            "statistics": "post_limit_statistics",
        }[post_contract.mode]]
    raw_context_capabilities = payload.get("context_capabilities")
    if not isinstance(raw_context_capabilities, list):
        raw_context_capabilities = []
    context_capabilities = [
        str(item)
        for item in raw_context_capabilities
        if isinstance(item, str) and item in context.last_capabilities
    ]
    # Only an explicit source-refinement follow-up inherits earlier evidence
    # capabilities. A new question must not inherit unrelated tools by accident.
    if context_mode == "source_refinement":
        raw_capabilities = [*context_capabilities, *raw_capabilities]
    else:
        context_capabilities = []
    raw_capabilities, tool_calls = _normalize_market_event_plan(
        request,
        raw_capabilities,
        tool_calls,
    )
    raw_capabilities, tool_calls = _normalize_broad_sector_plan(
        request,
        raw_capabilities,
        tool_calls,
    )
    raw_capabilities, tool_calls = _normalize_explicit_stock_evidence_plan(
        request,
        raw_capabilities,
        tool_calls,
    )
    if _looks_like_broad_sector_ranking_question(request.message):
        payload["intent_label"] = "sector_performance"
    if (
        extract_market_event_type(request.message) == "limit_down"
        and looks_like_market_event_query(request.message)
    ):
        payload["intent_label"] = "market_event_query"
    payload["context_mode"] = context_mode
    payload["context_capabilities"] = context_capabilities
    payload["raw_capabilities"] = raw_planner_capabilities
    payload["raw_tool_calls"] = raw_planner_tool_calls
    payload["capabilities"] = raw_capabilities
    policy_capabilities = normalize_capabilities(raw_capabilities)
    capabilities = normalize_capabilities(
        raw_capabilities,
        tool_calls=tool_calls,
    )
    payload["capabilities"] = list(capabilities)
    tool_calls = ensure_capability_tool_calls(
        capabilities,
        tool_calls,
        allowed_tool_names=tools.enabled_tool_names,
    )
    tool_calls = _normalize_explicit_stock_tool_calls(request, tool_calls)
    # The standalone planner API is also used by live eval. Compile the same
    # deterministic limit-up Query Contract that the production execution path
    # applies, so capability-first plans retain date/board/status/list semantics.
    if any(call.get("name") == "limit_up_events" for call in tool_calls) and (
        _looks_like_general_limit_up_question(request.message)
        or looks_like_limit_up_sector_summary_question(request.message)
        or looks_like_named_limit_up_sector_list_question(request.message)
    ):
        compiled_arguments = _limit_up_query_arguments_from_message(request)
        for call in tool_calls:
            if call.get("name") == "limit_up_events":
                call["arguments"] = compiled_arguments
    # Capability-first plans intentionally carry no raw arguments. Re-run the
    # deterministic compiler after tool injection so explicit user windows survive.
    tool_calls = _normalize_daily_board_promotion_tool_calls(
        request,
        tool_calls,
        default_days=context.promotion_days or 5,
    )
    payload["tool_calls"] = tool_calls
    return AgentQueryPlan(
        payload=payload,
        tool_calls=tool_calls,
        capabilities=capabilities,
        policy_capabilities=policy_capabilities,
        context_mode=context_mode,
        direct_answer=direct_answer,
        result=result,
        duration_ms=duration_ms,
        prompt_chars=prompt_chars,
    )






















class _SessionContext:
    """Minimal chat context recovered from recent Agent runs."""

    # Initialize _SessionContext with the supplied dependencies and per-instance state.
    def __init__(
        self,
        symbol: str | None = None,
        anchor_date: date | None = None,
        trade_date: date | None = None,
        filter_query: _FirstBoardFilterQuery | None = None,
        matched_symbols: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        last_capabilities: list[str] | None = None,
        last_intent: str | None = None,
        promotion_days: int | None = None,
        promotion_symbols: list[str] | None = None,
        session_memory: dict[str, Any] | None = None,
    ):
        self.symbol = symbol
        self.anchor_date = anchor_date
        self.trade_date = trade_date
        self.filter_query = filter_query
        self.matched_symbols = matched_symbols or []
        self.conversation_history = conversation_history or []
        self.last_capabilities = last_capabilities or []
        self.last_intent = last_intent
        self.promotion_days = promotion_days
        self.promotion_symbols = promotion_symbols or []
        self.session_memory = session_memory






















def _build_session_context(
    recent_runs: list[AgentRun],
    conversation_messages: list[ChatSessionMessage] | None = None,
    *,
    session_memory: ChatSessionMemory | None = None,
) -> _SessionContext:
    """Recover useful symbols, dates and filters from recent successful runs."""

    history = [
        {
            "role": message.role,
            "content": " ".join(message.content.split())[:400],
        }
        for message in (conversation_messages or [])[-8:]
        if message.status == "success" and message.content.strip()
    ]
    last_capabilities: list[str] = []
    for message in reversed(conversation_messages or []):
        last_capabilities = _capabilities_from_saved_payload(message.metadata)
        if last_capabilities:
            break
    context = _SessionContext(
        conversation_history=history,
        last_capabilities=last_capabilities,
        session_memory=memory_prompt_payload(session_memory),
    )
    for run in recent_runs:
        if run.status != "success":
            continue
        _merge_context_from_run(context, run)
        if (
            context.symbol
            and context.trade_date
            and context.filter_query
            and context.matched_symbols
        ):
            break
    return context


def _merge_context_from_run(context: _SessionContext, run: AgentRun) -> None:
    """Merge context from one persisted Agent run without overwriting newer data."""

    input_json = run.input_json or {}
    output_json = run.output_json or {}
    if not context.last_capabilities:
        context.last_capabilities = _capabilities_from_saved_payload(output_json)
    if context.last_intent is None and run.intent:
        context.last_intent = run.intent
    if context.symbol is None:
        context.symbol = input_json.get("symbol")
    if context.trade_date is None:
        context.trade_date = _parse_optional_date(input_json.get("trade_date"))

    stock_mentions = output_json.get("stock_mentions") or []
    if context.symbol is None and len(stock_mentions) == 1:
        mention = stock_mentions[0]
        if isinstance(mention, dict) and mention.get("symbol"):
            context.symbol = str(mention["symbol"])

    for reference in output_json.get("references", []) or []:
        if context.symbol is None and reference.startswith("symbol="):
            context.symbol = reference.split("=", 1)[1]
        elif context.trade_date is None and reference.startswith("trade_date="):
            context.trade_date = _parse_optional_date(reference.split("=", 1)[1])
        elif context.filter_query is None and reference.startswith("filter="):
            label = reference.split("=", 1)[1]
            context.filter_query = _filter_query_from_context(label)

    for tool_result in output_json.get("tool_results", []) or []:
        tool_input = tool_result.get("input", {})
        if tool_result.get("name") == "agent_plan":
            if context.symbol is None:
                context.symbol = tool_input.get("symbol")
            if context.trade_date is None:
                context.trade_date = _parse_optional_date(tool_input.get("trade_date"))
            if context.filter_query is None and tool_input.get("filter"):
                context.filter_query = _filter_query_from_context(tool_input["filter"])
        if tool_result.get("name") == "first_board_filter":
            if context.filter_query is None and tool_input.get("label"):
                aliases = tuple(tool_input.get("aliases") or (tool_input["label"],))
                context.filter_query = _FirstBoardFilterQuery(
                    label=tool_input["label"],
                    aliases=aliases,
                )
            if not context.matched_symbols:
                context.matched_symbols = list(tool_input.get("matched_symbols") or [])
        if tool_result.get("name") == "daily_board_promotion":
            if context.promotion_days is None:
                try:
                    context.promotion_days = max(
                        1,
                        min(int(tool_input.get("days")), 60),
                    )
                except (TypeError, ValueError):
                    pass
            if not context.promotion_symbols:
                tool_output = tool_result.get("output") or {}
                context.promotion_symbols = list(
                    dict.fromkeys(
                        str(stock.get("symbol"))
                        for item in tool_output.get("items") or []
                        if isinstance(item, dict)
                        for stock in item.get("promoted_stocks") or []
                        if isinstance(stock, dict)
                        and stock.get("symbol")
                        and int(stock.get("from_board_height") or 0) == 1
                        and int(stock.get("to_board_height") or 0) == 2
                    )
                )
        if tool_result.get("name") == "limit_up_events":
            query_contract = tool_input.get("query_contract") or {}
            if context.trade_date is None:
                context.trade_date = _parse_optional_date(
                    query_contract.get("trade_date") or tool_input.get("trade_date")
                )
            if context.filter_query is None and query_contract.get("query"):
                context.filter_query = _filter_query_from_context(
                    str(query_contract["query"])
                )
            if not context.matched_symbols:
                context.matched_symbols = [
                    str(item.get("symbol"))
                    for item in (tool_result.get("output", {}).get("events", []) or [])
                    if isinstance(item, dict) and item.get("symbol")
                ]
        if tool_result.get("name") in {"post_limit_screen", "post_limit_path"}:
            tool_output = tool_result.get("output") or {}
            if context.symbol is None:
                context.symbol = tool_input.get("symbol")
            if context.anchor_date is None:
                anchor = tool_output.get("anchor") or {}
                context.anchor_date = _parse_optional_date(
                    anchor.get("anchor_date") or tool_input.get("anchor_date")
                )
            candidates = tool_output.get("candidates") or []
            if len(candidates) == 1 and isinstance(candidates[0], dict):
                if context.symbol is None:
                    context.symbol = str(candidates[0].get("symbol") or "") or None
                if context.anchor_date is None:
                    context.anchor_date = _parse_optional_date(
                        candidates[0].get("anchor_date")
                    )


def _capabilities_from_saved_payload(payload: dict[str, Any] | None) -> list[str]:
    """Recover normalized Planner capabilities from persisted response metadata."""

    if not isinstance(payload, dict):
        return []
    direct = payload.get("capabilities")
    if isinstance(direct, list):
        values = [str(item) for item in direct if isinstance(item, str)]
        if values:
            return values
    for tool_result in payload.get("tool_results", []) or []:
        if not isinstance(tool_result, dict):
            continue
        if tool_result.get("name") != "llm_tool_planner":
            continue
        tool_input = tool_result.get("input") or {}
        capabilities = tool_input.get("capabilities") or []
        if isinstance(capabilities, list):
            return [str(item) for item in capabilities if isinstance(item, str)]
    tool_capabilities = {
        "post_limit_screen": "post_limit_screening",
        "post_limit_path": "post_limit_path",
        "post_limit_statistics": "post_limit_statistics",
    }
    return list(dict.fromkeys(
        tool_capabilities[tool_result.get("name")]
        for tool_result in payload.get("tool_results", []) or []
        if isinstance(tool_result, dict) and tool_result.get("name") in tool_capabilities
    ))


def _looks_like_post_limit_context_followup(
    message: str,
    context: _SessionContext,
) -> bool:
    """Recognize a pronoun follow-up after a grounded post-limit answer."""

    if not set(context.last_capabilities) & {
        "post_limit_screening", "post_limit_path", "post_limit_statistics"
    }:
        return False
    compact = re.sub(r"\s+", "", message)
    return any(term in compact for term in ("这只", "它", "该股", "这票")) and any(
        term in compact for term in ("为什么入选", "入选原因", "怎么走", "走势", "路径", "量比", "回撤")
    )
































































def _looks_like_general_limit_up_question(message: str) -> bool:
    """Return whether the user asks for same-day limit-up event lists."""

    normalized = message.lower()
    if looks_like_limit_up_sector_summary_question(message):
        return True
    if looks_like_named_limit_up_sector_list_question(message):
        return True
    if _looks_like_daily_board_promotion_question(message):
        return False
    if "\u9996\u677f" in normalized:
        return any(
            term in normalized
            for term in ("所有", "全部", "列出", "名单", "有哪些", "多少只", "有几只")
        )
    return any(
        keyword in normalized
        for keyword in (
            "\u8fde\u677f",
            "\u4e8c\u677f",
            "\u4e8c\u8fde",
            "\u4e09\u677f",
            "\u4e09\u8fde",
            "\u56db\u677f",
            "\u4e94\u677f",
            "\u6700\u9ad8\u677f",
            "\u68af\u961f",
            "\u6da8\u505c\u7684\u7968",
            "\u6da8\u505c\u7968",
            "\u70b8\u677f",
        )
    )


def _limit_up_query_arguments_from_message(request: AgentChatRequest) -> dict[str, Any]:
    """Build limit-up event tool arguments from a user question."""

    filter_query = _extract_first_board_filter(request.message)
    return build_limit_up_query_contract(
        request.message,
        request_trade_date=request.trade_date,
        planner_arguments={"query": filter_query.label} if filter_query else None,
    ).to_tool_arguments()
