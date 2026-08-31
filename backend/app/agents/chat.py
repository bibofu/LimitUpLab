"""Tool-grounded first-board chat agent."""

import json
import math
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from time import perf_counter
from typing import Any, Callable, Iterator

from app.agents.explanation import explain_first_board_rating
from app.agents.capability_contract import (
    capability_schema_prompt,
    ensure_capability_tool_calls,
    normalize_capabilities,
)
from app.agent_output_sanitizer import (
    AgentAnswerStreamSanitizer,
    friendly_tool_label,
)
from app.agents.query_contract import (
    MARKET_SEGMENT_LABELS,
    build_limit_up_query_contract,
    extract_board_filters as contract_board_filters,
    extract_result_limit,
    looks_like_exhaustive_request,
)
from app.agents.skills import AGENT_SKILL_REGISTRY, AgentSkill
from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals as _QuestionSignals,
    ToolExecution,
    extract_market_index_days as _extract_market_index_days,
    extract_kline_days as _extract_kline_days,
    extract_stock_news_days as _extract_stock_news_days,
    extract_promotion_days as _extract_promotion_days,
    extract_sector_query as _extract_sector_query,
    extract_trade_date as _extract_trade_date,
    looks_like_critic_question as _looks_like_critic_question,
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_evaluation_question as _looks_like_evaluation_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
    looks_like_limit_up_event_question as _looks_like_limit_up_event_question,
    looks_like_rating_backtest_question as _looks_like_rating_backtest_question,
    looks_like_rating_explain_question as _looks_like_rating_explain_question,
    looks_like_review_question as _looks_like_review_question,
    looks_like_stock_kline_question as _looks_like_stock_kline_question,
)
from app.agents.tools import (
    AgentToolRegistry,
    EXTENDED_AGENT_PROFILE,
    ToolResult,
    compact_first_board_position_groups,
    compact_prediction_quality_audit,
)
from app.models import (
    AgentChatRequest,
    AgentChatPerformance,
    AgentChatResponse,
    AgentToolTrace,
    AgentRun,
    ChatSessionMessage,
    build_agent_evidence_cards,
    build_agent_tool_policy_audit,
    FirstBoardDiscoveryResponse,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketIndexTrendFacts,
    MarketSummary,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, get_llm_provider


CHAT_AGENT_VERSION = "first-board-chat-policy-v10-stock-news"
_FORCE_TEMPLATE_ANSWER_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "force_template_answer_override",
    default=None,
)
SEMI = "\uff1b"
IDEOGRAPHIC_COMMA = "\u3001"
UNANSWERABLE_TEXT = "抱歉，该问题无法回答"
PLANNER_DIRECT_ANSWER_INTENTS = {"capability_intro", "greeting", "smalltalk"}
SUPPORTED_INTENTS = {
    "capability_intro",
    "greeting",
    "smalltalk",
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
    "limit_up_query",
    "today_summary",
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


def _template_answer_forced() -> bool:
    """Resolve the context-local override before the process startup setting."""

    override = _FORCE_TEMPLATE_ANSWER_OVERRIDE.get()
    if override is not None:
        return override
    return os.getenv("LIMITUPLAB_FORCE_TEMPLATE_ANSWER", "").lower() in {
        "1",
        "true",
        "yes",
    }

TEXT = {
    "greeting": "你好，我是 LimitUpLab 的首板 Agent。我可以总结今日首板、解释个股评分、分析风险，也可以查询热门股票、财经快讯、个股新闻、个股走势和市场环境。",
    "capability": "我是 LimitUpLab V1 首板复盘 Agent。我使用最新完整收盘数据和个股日 K 线生成首板挖掘与一进二接力两类 Top10 观察名单，并复盘 D+1 至 D+5 走势、晋级率和评分表现；也可以按需查询带来源和时间的热门股票榜单、财经快讯、个股新闻及近期动态。我不提供盘中实时行情、买卖指令、仓位、目标价或收益承诺。",
    "smalltalk": "我在。你可以直接问首板候选、板块分布、评分理由、风险或个股走势。",
    "out_of_scope": UNANSWERABLE_TEXT,
    "unsafe": "我不能给出直接交易指令、资金配比、价格预测或回报承诺。我可以基于结构化数据分析评分理由、风险、板块热度和市场环境。",
    "unknown": UNANSWERABLE_TEXT,
    "safety": "\u4ee5\u4e0a\u4e3a\u57fa\u4e8e\u672c\u5730\u7ed3\u6784\u5316\u6570\u636e\u7684\u590d\u76d8\u5206\u6790\uff0c\u4e0d\u6784\u6210\u4e70\u5356\u5efa\u8bae\u3002",
}

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

class _FirstBoardFilterQuery:
    """Structured filter parsed from a first-board natural-language question."""

    def __init__(self, label: str, aliases: tuple[str, ...]):
        self.label = label
        self.aliases = aliases


class _AgentPlan:
    """Deterministic tool plan produced before answer generation."""

    def __init__(
        self,
        intent: str,
        trade_date: date | None,
        parsed_trade_date: date | None,
        filter_query: _FirstBoardFilterQuery | None,
        symbol: str | None,
        tool_steps: list[dict],
        rationale: str,
    ):
        self.intent = intent
        self.trade_date = trade_date
        self.parsed_trade_date = parsed_trade_date
        self.filter_query = filter_query
        self.symbol = symbol
        self.tool_steps = tool_steps
        self.rationale = rationale

    def trace(self) -> AgentToolTrace:
        """Return the plan as a compact trace for debugging and UI display."""

        return AgentToolTrace(
            name="agent_plan",
            input={
                "intent": self.intent,
                "trade_date": self.trade_date.isoformat() if self.trade_date else None,
                "parsed_trade_date": (
                    self.parsed_trade_date.isoformat()
                    if self.parsed_trade_date
                    else None
                ),
                "filter": self.filter_query.label if self.filter_query else None,
                "symbol": self.symbol,
                "tool_steps": self.tool_steps,
            },
            summary=self.rationale,
        )


class AgentQueryPlan:
    """Normalized LLM plan shared by runtime execution and semantic evals."""

    def __init__(
        self,
        *,
        payload: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        capabilities: tuple[str, ...],
        policy_capabilities: tuple[str, ...],
        context_mode: str,
        active_skill: AgentSkill | None,
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
        self.active_skill = active_skill
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
    repository: SQLiteFirstBoardRepository | None = None,
) -> AgentQueryPlan:
    """Run only the production LLM planning stage without executing evidence tools."""

    tools = AgentToolRegistry(
        events=events,
        first_board_repository=repository or SQLiteFirstBoardRepository(),
    )
    context = _build_session_context([], conversation_messages or [])
    return _generate_llm_query_plan(request, tools, context, llm_provider)


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository | None = None,
    recent_runs: list[AgentRun] | None = None,
    conversation_messages: list[ChatSessionMessage] | None = None,
    llm_provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
) -> AgentChatResponse:
    """Answer a user question with LLM-planned tools and deterministic fallback."""

    active_repository = repository or SQLiteFirstBoardRepository()
    tools = AgentToolRegistry(events=events, first_board_repository=active_repository)
    context = _build_session_context(
        recent_runs or [],
        conversation_messages or [],
    )
    if (
        tools.profile != EXTENDED_AGENT_PROFILE
        and _requires_deferred_v1_capability(request.message)
    ):
        return _answer_static_text(
            request,
            "out_of_scope",
            UNANSWERABLE_TEXT,
        )
    if _looks_like_retired_case_retrieval_question(request.message):
        return _answer_static_text(
            request,
            "out_of_scope",
            "历史相似案例功能已经下线。你可以改为询问这只股票的评分依据、主要风险、近期 K 线走势，或查看高分票追踪复盘。",
        )
    llm_response = _answer_with_llm_tool_agent(
        request=request,
        tools=tools,
        context=context,
        provider=llm_provider,
        progress_callback=progress_callback,
        answer_delta_callback=answer_delta_callback,
    )
    if llm_response is not None:
        return llm_response
    daily_promotion_fallback = _answer_daily_board_promotion_without_llm(
        request=request,
        tools=tools,
    )
    if daily_promotion_fallback is not None:
        return daily_promotion_fallback
    prediction_quality_fallback = _answer_prediction_quality_without_llm(
        request=request,
        tools=tools,
    )
    if prediction_quality_fallback is not None:
        return prediction_quality_fallback
    market_index_fallback = _answer_market_index_trend_without_llm(
        request=request,
        tools=tools,
    )
    if market_index_fallback is not None:
        return market_index_fallback
    stock_kline_fallback = _answer_stock_kline_without_llm(
        request=request,
        tools=tools,
        context=context,
    )
    if stock_kline_fallback is not None:
        return stock_kline_fallback

    plan = _build_agent_plan(request=request, context=context)
    intent = plan.intent
    trade_date = plan.trade_date
    first_board_filter = plan.filter_query

    if intent == "capability_intro":
        return _with_plan_trace(_answer_static_text(request, "capability_intro", TEXT["capability"]), plan)
    if intent == "greeting":
        return _with_plan_trace(_answer_greeting(request), plan)
    if intent == "smalltalk":
        return _with_plan_trace(_answer_static_text(request, "smalltalk", TEXT["smalltalk"]), plan)
    if intent == "unsafe_investment_advice":
        return _with_plan_trace(_answer_static_text(request, "unsafe_investment_advice", TEXT["unsafe"]), plan)
    if intent == "out_of_scope":
        return _with_plan_trace(_answer_static_text(request, "out_of_scope", TEXT["out_of_scope"]), plan)
    if intent == "unknown":
        return _with_plan_trace(
            _answer_static_text(request, "unknown", UNANSWERABLE_TEXT),
            plan,
        )
    if intent == "market_schedule":
        latest_tool = tools.market_summary()
        return _with_plan_trace(
            _answer_market_schedule(request, latest_tool.output.trade_date),
            plan,
        )
    if trade_date and not _has_events_for_date(events, trade_date):
        return _with_plan_trace(
            _answer_missing_trade_date(request, trade_date, events),
            plan,
        )
    if intent == "limit_up_query":
        return _with_plan_trace(
            _answer_limit_up_query(request, tools, trade_date, first_board_filter),
            plan,
        )

    ratings_tool = tools.first_board_ratings(trade_date=trade_date)
    ratings = ratings_tool.output
    symbol = _resolve_symbol(
        message=request.message,
        context_symbol=plan.symbol or request.symbol or context.symbol,
        candidates=ratings.candidates,
    )
    plan.symbol = symbol or plan.symbol

    if intent == "market_context":
        return _with_plan_trace(
            _answer_tool_grounded_question(
                request=request,
                tools=tools,
                ratings_tool=ratings_tool,
                filter_query=first_board_filter,
                symbol=symbol,
                intent=intent,
            ),
            plan,
        )
    if intent == "llm_explanation" and symbol:
        return _with_plan_trace(
            _answer_llm_explanation(request, symbol, ratings, tools),
            plan,
        )
    if intent == "first_board_context_top" and first_board_filter:
        return _with_plan_trace(
            _answer_first_board_context_top(
                request=request,
                ratings_tool=ratings_tool,
                filter_query=first_board_filter,
                context_symbols=context.matched_symbols,
            ),
            plan,
        )
    if intent == "first_board_sector_summary":
        return _with_plan_trace(
            _answer_tool_grounded_question(
                request=request,
                tools=tools,
                ratings_tool=ratings_tool,
                filter_query=first_board_filter,
                symbol=symbol,
                intent=intent,
            ),
            plan,
        )
    if first_board_filter and _looks_like_first_board_data_question(request.message):
        return _with_plan_trace(
            _answer_first_board_filter(request, ratings_tool, first_board_filter),
            plan,
        )
    if intent == "first_board_filter":
        if first_board_filter is None:
            first_board_filter = _FirstBoardFilterQuery(
                label="\u7528\u6237\u95ee\u53e5",
                aliases=(request.message.strip(),),
            )
            plan.filter_query = first_board_filter
        return _with_plan_trace(
            _answer_first_board_filter(request, ratings_tool, first_board_filter),
            plan,
        )
    if intent == "rating_explain" and symbol:
        return _with_plan_trace(
            _answer_rating_explain(request, symbol, ratings.candidates),
            plan,
        )
    if intent == "risk_summary" and symbol:
        return _with_plan_trace(
            _answer_risk_summary(request, symbol, ratings.candidates),
            plan,
        )
    if intent == "today_summary":
        return _with_plan_trace(
            _answer_tool_grounded_question(
                request=request,
                tools=tools,
                ratings_tool=ratings_tool,
                filter_query=first_board_filter,
                symbol=symbol,
                intent=intent,
            ),
            plan,
        )
    if symbol:
        return _with_plan_trace(
            _answer_rating_explain(request, symbol, ratings.candidates),
            plan,
        )

    return _with_plan_trace(
        _answer_tool_grounded_question(
            request=request,
            tools=tools,
            ratings_tool=ratings_tool,
            filter_query=first_board_filter,
            symbol=symbol,
            intent=intent,
        ),
        plan,
    )


def _requires_deferred_v1_capability(message: str) -> bool:
    """Return whether a question requires real-time or external V2 evidence."""

    signals = _QuestionSignals.from_message(message)
    if any(
        (
            signals.web_search,
        )
    ):
        return True
    compact = re.sub(r"\s+", "", message.lower())
    realtime_terms = (
        "实时",
        "盘中",
        "分时",
        "集合竞价",
        "竞价",
        "当前价格",
        "现在价格",
        "最新价格",
    )
    return any(term in compact for term in realtime_terms)


def _answer_with_llm_tool_agent(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    context: "_SessionContext",
    provider: LLMProvider | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    answer_delta_callback: Callable[[str], None] | None = None,
) -> AgentChatResponse | None:
    """Let the LLM choose tools first, then answer from executed tool facts."""

    deterministic = _deterministic_pre_llm_response(request, tools.events)
    if deterministic is not None:
        return deterministic

    agent_started_at = perf_counter()
    active_provider = provider or get_llm_provider()
    policy = AgentToolPolicyEngine(tools, compact_ratings=_compact_ratings_facts)
    if progress_callback:
        progress_callback("planning", "正在理解问题并选择所需工具")
    try:
        query_plan = _generate_llm_query_plan(
            request,
            tools,
            context,
            active_provider,
        )
    except Exception:
        return None
    tool_plan = query_plan.payload
    plan_result = query_plan.result
    planner_duration_ms = query_plan.duration_ms
    planner_prompt_chars = query_plan.prompt_chars

    safety = str(tool_plan.get("safety", "normal"))
    intent = str(tool_plan.get("intent_label") or "llm_tool_agent")
    if safety == "refuse_trade_instruction":
        return _answer_static_text(
            request,
            "unsafe_investment_advice",
            TEXT["unsafe"],
        )

    tool_calls = query_plan.tool_calls
    direct_answer = query_plan.direct_answer
    active_skill = query_plan.active_skill
    capabilities = query_plan.capabilities
    policy_capabilities = query_plan.policy_capabilities
    if intent == "out_of_scope":
        return _unanswerable_response(
            request=request,
            intent=intent,
            tool_calls=["llm_tool_planner"],
            tool_results=[
                _llm_plan_trace(
                    tool_plan,
                    plan_result.model,
                    plan_result.provider,
                    planner_duration_ms,
                    planner_prompt_chars,
                    plan_result.completion_chars,
                )
            ],
            performance=AgentChatPerformance(
                planner_duration_ms=planner_duration_ms,
                total_duration_ms=round((perf_counter() - agent_started_at) * 1000),
                planner_prompt_chars=planner_prompt_chars,
            ),
        )
    if not tool_calls and _looks_like_general_limit_up_question(request.message):
        tool_calls = [
            {
                "name": "limit_up_events",
                "arguments": _limit_up_query_arguments_from_message(request),
            }
        ]
        direct_answer = ""
    if not tool_calls and direct_answer and policy.requires_grounding(
        request,
        policy_capabilities,
    ):
        direct_answer = ""
    if not tool_calls and direct_answer and intent not in PLANNER_DIRECT_ANSWER_INTENTS:
        direct_answer = ""
    if not tool_calls and direct_answer:
        requested_date = _extract_trade_date(request.message)
        if requested_date and not _has_events_for_date(tools.events, requested_date):
            direct_answer = ""
            tool_calls = []
        else:
            return AgentChatResponse(
                session_id=request.session_id,
                intent=intent,
                answer=_ensure_safety_boundary(direct_answer),
                tool_calls=["llm_planner_direct_answer"],
                tool_results=[
                    _llm_plan_trace(
                        tool_plan,
                        plan_result.model,
                        plan_result.provider,
                        planner_duration_ms,
                        planner_prompt_chars,
                        plan_result.completion_chars,
                    )
                ],
                references=[],
                warnings=[_safety_warning()],
                performance=AgentChatPerformance(
                    planner_duration_ms=planner_duration_ms,
                    total_duration_ms=round((perf_counter() - agent_started_at) * 1000),
                    planner_prompt_chars=planner_prompt_chars,
                ),
                generated_by=CHAT_AGENT_VERSION,
            )
    if not tool_calls and not direct_answer and _extract_trade_date(request.message):
        requested_date = _extract_trade_date(request.message)
        if requested_date and not _has_events_for_date(tools.events, requested_date):
            available_dates = sorted({event.trade_date for event in tools.events}, reverse=True)
            answer = _answer_missing_trade_date(request, requested_date, tools.events)
            return AgentChatResponse(
                session_id=request.session_id,
                intent="data_availability",
                answer=answer.answer,
                tool_calls=["llm_tool_planner", "limit_up_event_dates"],
                tool_results=[
                    _llm_plan_trace(
                        tool_plan,
                        plan_result.model,
                        plan_result.provider,
                        planner_duration_ms,
                        planner_prompt_chars,
                        plan_result.completion_chars,
                    ),
                    AgentToolTrace(
                        name="limit_up_event_dates",
                        input={"requested_trade_date": requested_date.isoformat()},
                        summary=f"本地没有 {requested_date.isoformat()}，已返回可用交易日列表。",
                        output={
                            "requested_trade_date": requested_date.isoformat(),
                            "latest_local_trade_date": (
                                available_dates[0].isoformat() if available_dates else None
                            ),
                            "available_trade_dates": [
                                item.isoformat() for item in available_dates[:20]
                            ],
                        },
                    ),
                ],
                references=[f"missing_trade_date={requested_date.isoformat()}"],
                warnings=answer.warnings,
                performance=AgentChatPerformance(
                    planner_duration_ms=planner_duration_ms,
                    total_duration_ms=round((perf_counter() - agent_started_at) * 1000),
                    planner_prompt_chars=planner_prompt_chars,
                ),
                generated_by=CHAT_AGENT_VERSION,
            )
    if (
        _looks_like_general_limit_up_question(request.message)
        and not any(call.get("name") == "limit_up_events" for call in tool_calls)
    ):
        tool_calls.insert(
            0,
            {
                "name": "limit_up_events",
                "arguments": _limit_up_query_arguments_from_message(request),
            },
        )

    tools_started_at = perf_counter()
    if progress_callback:
        selected_tools = "、".join(
            friendly_tool_label(str(call.get("name")))
            for call in tool_calls
            if call.get("name")
        )
        progress_callback(
            "tools",
            f"正在查询 {selected_tools}" if selected_tools else "正在查询本地事实数据",
        )
    execution = _execute_llm_tool_calls(
        tool_calls,
        tools,
        request=request,
        context_symbol=context.symbol,
    )
    policy.reconcile(
        request=request,
        execution=execution,
        context_symbol=context.symbol,
        capabilities=policy_capabilities,
    )
    _add_composed_tool_facts(request.message, execution["facts"])
    active_skill = active_skill or AGENT_SKILL_REGISTRY.resolve_from_facts(
        execution["facts"],
        tools.enabled_tool_names,
    )
    if active_skill is not None:
        tool_plan["skill_name"] = active_skill.name
    tool_duration_ms = round((perf_counter() - tools_started_at) * 1000)
    if not _has_usable_tool_facts(execution["facts"]):
        return _unanswerable_response(
            request=request,
            intent=intent,
            tool_calls=["llm_tool_planner", *execution["tool_call_names"]],
            tool_results=[
                _llm_plan_trace(
                    tool_plan,
                    plan_result.model,
                    plan_result.provider,
                    planner_duration_ms,
                    planner_prompt_chars,
                    plan_result.completion_chars,
                ),
                *execution["tool_results"],
            ],
            references=execution["references"],
            warnings=["No successful evidence was available for this question."],
            performance=AgentChatPerformance(
                planner_duration_ms=planner_duration_ms,
                tool_duration_ms=tool_duration_ms,
                total_duration_ms=round((perf_counter() - agent_started_at) * 1000),
                planner_prompt_chars=planner_prompt_chars,
            ),
        )

    fallback = direct_answer or _template_answer_from_tool_facts(
        request=request,
        intent=intent,
        facts=execution["facts"],
    )
    exhaustive_event_answer = _requires_exhaustive_event_answer(
        request.message,
        execution["facts"],
    )
    complete_position_answer = _requires_complete_position_answer(
        request.message,
        execution["facts"],
    )
    complete_hot_stock_answer = _requires_complete_hot_stock_answer(
        request.message,
        execution["facts"],
    )
    hot_stock_event_intersection_answer = (
        _requires_hot_stock_event_intersection_answer(execution["facts"])
    )
    daily_promotion_answer = _requires_daily_promotion_answer(
        request.message,
        execution["facts"],
    )
    high_score_promotion_answer = _requires_high_score_promotion_answer(
        request.message,
        execution["facts"],
    )
    market_environment_answer = (
        "market_environment" in capabilities
        or _QuestionSignals.from_message(request.message).market_environment
    )
    answer_system_prompt = _tool_answer_system_prompt(
        agent_profile=tools.profile,
        exhaustive_event_answer=exhaustive_event_answer,
        complete_position_answer=complete_position_answer,
        complete_hot_stock_answer=complete_hot_stock_answer,
        hot_stock_event_intersection_answer=hot_stock_event_intersection_answer,
        market_environment_answer=market_environment_answer,
        skill_instruction=(
            active_skill.answer_instruction() if active_skill is not None else ""
        ),
    )
    answer_user_prompt = _tool_answer_user_prompt(
        request,
        tool_plan,
        execution["facts"],
        context,
    )
    answer_started_at = perf_counter()
    final_result = None
    if progress_callback:
        progress_callback("answering", "正在基于工具事实生成回答")
    if _template_answer_forced():
        answer = _ensure_safety_boundary(fallback)
        answer = _ensure_explicit_symbol_mentioned(request, answer)
        source = "template_general_answer"
        warnings = [_safety_warning()]
    else:
        try:
            if answer_delta_callback:
                stream_sanitizer = AgentAnswerStreamSanitizer(answer_delta_callback)
                final_result = active_provider.stream_generate(
                    answer_system_prompt,
                    answer_user_prompt,
                    stream_sanitizer.feed,
                )
                stream_sanitizer.flush()
            else:
                final_result = active_provider.generate(
                    answer_system_prompt,
                    answer_user_prompt,
                )
            answer = _ensure_safety_boundary(final_result.content)
            source = "llm_tool_answer"
            warnings = [_safety_warning()]
            if exhaustive_event_answer and not _contains_every_event_symbol(
                answer,
                execution["facts"],
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM exhaustive list was incomplete; deterministic full-list rendering used.",
                ]
            if complete_position_answer and not _contains_complete_position_groups(
                answer,
                execution["facts"],
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM position classification was incomplete; deterministic complete grouping used.",
                ]
            if complete_hot_stock_answer and not _contains_every_hot_stock_symbol(
                answer,
                execution["facts"],
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM hot-stock ranking was incomplete; deterministic full-list rendering used.",
                ]
            if (
                hot_stock_event_intersection_answer
                and not _contains_exact_hot_stock_event_intersection(
                    answer,
                    execution["facts"],
                )
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM cross-list filtering was incorrect; deterministic intersection rendering used.",
                ]
            if daily_promotion_answer and not _contains_daily_promotion_facts(
                answer,
                execution["facts"],
                request.message,
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM daily promotion answer was incomplete; deterministic statistics used.",
                ]
            if high_score_promotion_answer and not _contains_high_score_promotion_facts(
                answer,
                execution["facts"],
            ):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM high-score promotion comparison was incomplete; deterministic statistics used.",
                ]
            answer = _ensure_explicit_symbol_mentioned(request, answer)
            if _contains_forbidden_terms(answer):
                answer = _ensure_safety_boundary(fallback)
                source = "template_general_answer"
                warnings = [
                    _safety_warning(),
                    "LLM output failed safety validation; template fallback used.",
                ]
        except Exception as error:
            answer = _ensure_safety_boundary(fallback)
            answer = _ensure_explicit_symbol_mentioned(request, answer)
            source = "template_general_answer"
            warnings = [
                _safety_warning(),
                f"LLM unavailable during final answer; template fallback used: {error}",
            ]
    answer_duration_ms = (
        final_result.duration_ms
        if final_result and final_result.duration_ms
        else round((perf_counter() - answer_started_at) * 1000)
    )
    answer_prompt_chars = (
        final_result.prompt_chars
        if final_result and final_result.prompt_chars
        else len(answer_system_prompt) + len(answer_user_prompt)
    )

    return AgentChatResponse(
        session_id=request.session_id,
        intent=intent,
        answer=answer,
        tool_calls=[
            "llm_tool_planner",
            *execution["tool_call_names"],
            source,
        ],
        tool_results=[
            _llm_plan_trace(
                tool_plan,
                plan_result.model,
                plan_result.provider,
                planner_duration_ms,
                planner_prompt_chars,
                plan_result.completion_chars,
            ),
            *execution["tool_results"],
        ],
        references=execution["references"],
        warnings=warnings,
        performance=AgentChatPerformance(
            planner_duration_ms=planner_duration_ms,
            tool_duration_ms=tool_duration_ms,
            answer_duration_ms=answer_duration_ms,
            total_duration_ms=round((perf_counter() - agent_started_at) * 1000),
            planner_prompt_chars=planner_prompt_chars,
            answer_prompt_chars=answer_prompt_chars,
        ),
        generated_by=CHAT_AGENT_VERSION,
    )


def _generate_llm_query_plan(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    context: "_SessionContext",
    provider: LLMProvider,
) -> AgentQueryPlan:
    """Generate and normalize the production LLM query plan."""

    planner_system_prompt = _tool_planner_system_prompt(
        tools.schema_prompt(),
        AGENT_SKILL_REGISTRY.schema_prompt(tools.enabled_tool_names),
        capability_schema_prompt(tools.enabled_tool_names),
        tools.profile,
    )
    planner_user_prompt = _tool_planner_user_prompt(request, context, tools.events)
    started_at = perf_counter()
    result = provider.generate(planner_system_prompt, planner_user_prompt)
    payload = _parse_json_object(result.content)
    duration_ms = result.duration_ms or round((perf_counter() - started_at) * 1000)
    prompt_chars = result.prompt_chars or (
        len(planner_system_prompt) + len(planner_user_prompt)
    )

    tool_calls = _normalize_tool_calls(payload.get("tool_calls"))
    tool_calls = _normalize_first_board_position_tool_calls(request, tool_calls)
    tool_calls = _normalize_daily_board_promotion_tool_calls(request, tool_calls)
    direct_answer = str(payload.get("answer_directly") or "").strip()
    declared_skill_name = payload.get("skill_name")
    active_skill = AGENT_SKILL_REGISTRY.resolve(
        declared_skill_name,
        tool_calls,
        tools.enabled_tool_names,
    )
    if active_skill is not None:
        payload["skill_name"] = active_skill.name
        tool_calls = AGENT_SKILL_REGISTRY.ensure_required_tool_calls(
            active_skill,
            tool_calls,
        )
        direct_answer = ""
    context_mode = str(payload.get("context_mode") or "standalone").strip().lower()
    if context_mode not in {"standalone", "entity_followup", "source_refinement"}:
        context_mode = "standalone"
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list):
        raw_capabilities = []
    raw_context_capabilities = payload.get("context_capabilities")
    if not isinstance(raw_context_capabilities, list):
        raw_context_capabilities = []
    context_capabilities = [
        str(item)
        for item in raw_context_capabilities
        if isinstance(item, str) and item in context.last_capabilities
    ]
    if context_mode == "source_refinement":
        raw_capabilities = [*context_capabilities, *raw_capabilities]
    else:
        context_capabilities = []
    payload["context_mode"] = context_mode
    payload["context_capabilities"] = context_capabilities
    payload["capabilities"] = raw_capabilities
    policy_capabilities = normalize_capabilities(
        raw_capabilities,
        skill_name=declared_skill_name,
    )
    capabilities = normalize_capabilities(
        raw_capabilities,
        skill_name=payload.get("skill_name"),
        tool_calls=tool_calls,
    )
    payload["capabilities"] = list(capabilities)
    tool_calls = ensure_capability_tool_calls(
        capabilities,
        tool_calls,
        allowed_tool_names=tools.enabled_tool_names,
    )
    return AgentQueryPlan(
        payload=payload,
        tool_calls=tool_calls,
        capabilities=capabilities,
        policy_capabilities=policy_capabilities,
        context_mode=context_mode,
        active_skill=active_skill,
        direct_answer=direct_answer,
        result=result,
        duration_ms=duration_ms,
        prompt_chars=prompt_chars,
    )


def _tool_planner_system_prompt(
    tool_schema_prompt: str,
    skill_schema_prompt: str,
    capability_contract_prompt: str,
    agent_profile: str,
) -> str:
    """Describe the Agent tools and require a strict JSON tool plan."""

    profile_instruction = (
        "The extended preview profile may use every tool explicitly listed in the "
        "available schema, while still grounding every factual claim. "
        if agent_profile == EXTENDED_AGENT_PROFILE
        else (
            "V1 is an after-close review and next-day first-to-second-board candidate "
            "research product. Use only the latest complete close or stored historical "
            "facts exposed by the available tools, except that current stock-popularity "
            "questions may use the timestamped hot_stock_ranking snapshot and broad "
            "financial-news digests may use timestamped finance_news feeds, while named-stock "
            "news and after-close activity may use stock_news and stock_activity. Broad market "
            "reviews may also use the latest timestamped sector ranking. Never claim "
            "to have intraday prices, auction data, or arbitrary "
            "public-web evidence. Questions that require those deferred V2 capabilities "
            "are unsupported and must receive exactly '抱歉，该问题无法回答'. "
        )
    )
    return (
        "You are LimitUpLab's A-share first-board research agent. "
        f"The active product profile is {agent_profile}. "
        f"{profile_instruction}"
        "Your first job is to decide which tools are needed, not to answer directly "
        "unless the question is a greeting, capability question, or small talk. "
        "For an out-of-scope or unsupported question, set intent_label to out_of_scope "
        "and answer_directly to exactly '抱歉，该问题无法回答'. "
        "Choose one skill_name from the supplied skill catalog when a skill covers the "
        "question; otherwise use null. A skill is a business workflow, while tools provide facts. "
        "Translate every supported domain request into one or more normalized capabilities "
        "from the supplied capability catalog. Use multiple capabilities for compound questions, "
        "regardless of synonyms, colloquial wording, clause order, or omitted dates. "
        "Select the smallest sufficient capability set and never add a capability or tool only "
        "because a related noun appears. Avoid redundant tools. "
        "For follow-ups such as 这些, 其中, 上述, 刚才的, 再结合 or 一起讲, inspect "
        "recent_context.last_capabilities. Preserve and re-run the relevant previous "
        "source capabilities, then add the new capability needed for the requested join. "
        "Conversation text is not reusable evidence. If the user explicitly says 只看, "
        "discard unrelated previous capabilities and answer only the narrowed request. "
        "Set context_mode=source_refinement when the requested result is constrained by or "
        "combined with a previous result set. Put only the still-required previous source IDs "
        "in context_capabilities; the backend merges only those capabilities. "
        "Set context_mode=entity_followup for pronouns that only retain a stock, date or named "
        "entity but do not need the previous evidence source. Otherwise use standalone. "
        "Example: after popularity, '这些里面哪些涨停' => context_mode source_refinement, "
        "context_capabilities [popularity], capabilities [limit_up_pool]. After a broad market "
        "review, '强势行业展开' => entity_followup with capabilities [sector_performance]. "
        "When a skill is selected, include its required tools unless the backend can safely fill defaults. "
        "Return only valid JSON. No markdown. "
        f"Available skills: {skill_schema_prompt}. "
        f"Capability catalog: {capability_contract_prompt}. "
        f"Available tools are described as JSON schemas: {tool_schema_prompt}. "
        "Use YYYY-MM-DD for all dates. "
        "For capability questions, answer_directly must mention LimitUpLab. "
        "For rating explanation questions, first call first_board_ratings before critic tools. "
        "For next-session first-board discovery, call first_board_discovery; this is a pre-limit-up watchlist and is separate from first_board_ratings, which ranks stocks that already closed at first board for one-to-two continuation. "
        "For review questions about recent high-score picks, model performance, misses, scoring taste, or Top10 first-to-second-board success versus the market, call review_high_score_picks. "
        "Historical high-score performance and good/bad sample traits are prediction_review only; do not add first_board_rating unless the user separately asks for today's rating facts. "
        "A comparison of which current candidates or first-board samples have better quality is first_board_rating. prediction_review requires explicit realized-outcome language such as 后续表现, 走出来, 兑现, 命中 or 复盘过去结果. "
        "For scoring weights, strategy versions, autonomous learning, Champion, or Challenger questions, call scoring_policy_status. "
        "For daily limit-up promotion rates, first-board-to-second-board rates, or continued-board ladder success, call daily_board_promotion; do not infer rates from same-day counts. "
        "Questions asking how many stocks sealed yesterday continued to seal today are also board_promotion, not a same-day limit_up_pool list. "
        "An '一进二观察名单', candidate list, recommendation ranking, or Top10 means first_board_rating; historical realized one-to-two counts or rates mean board_promotion. "
        "For first-board position/location classification, position means the pre-board K-line regime such as low-base breakout, oversold rebound, V reversal, high breakout or second wave; call first_board_ratings and never classify by first seal time. "
        "For ordinary limit-up, first-board, or continued-board lists, call limit_up_events. Follow the backend_query_contract supplied with the user message for date, board height, market, event status, result mode, sorting and limit; do not weaken explicit user filters. Use first_board_ratings only when the user asks for ratings, scores, ranking, or candidate filtering. "
        "For Dragon-Tiger List, institution flow or hot-money flow questions, call dragon_tiger_list only for a completed trade date. "
        "For a theme or industry inside the local limit-up pool, call limit_up_events. For whole-market industry performance or ranking, call sector_performance and state its source, data_as_of and freshness. "
        "Grouping a previously returned stock set by its existing industry or concept fields does not require sector_performance; use it only for whole-market industry strength, return or ranking. "
        "For broad market-environment questions, select the market-environment skill and call market_summary with include_limit_down=true, market_index_trend for 5 trading days, sector_performance without a sector filter, and hot_stock_ranking with enrich_performance=true. Do not answer from only one of these evidence groups. "
        "For broad-market, major-index, Shanghai Composite, Shenzhen Component or ChiNext Index performance over multiple days, call market_index_trend with the requested trading-day window; never infer index performance from limit-up counts. "
        "If the user asks only for the market/index curve or index return, do not expand it into market_environment, sector_performance or popularity. "
        "For current hot, popular, popularity-ranked, or attention-ranked stocks, call hot_stock_ranking; default to 20 rows when the user gives no count, state the source and Beijing capture time, and say that popularity reflects attention and does not constitute a trading signal. In this answer, never use the exact Chinese tokens 买入, 卖出, 仓位, 目标价, or 收益承诺, even inside a disclaimer. "
        "For broad latest, today, or recent financial-news and market-flash questions, call finance_news with a 48-hour window and up to 8 items. In this financial product, an unqualified request such as 最新的新闻, 最近的消息, 有什么新闻 or 新闻摘要 means the broad finance_news capability unless the user names a company, sector, announcement or event. State the Beijing retrieval time and each item's publication time, source, title, concise summary and URL. Preserve the tool's item order and do not claim chronological ordering unless the timestamps actually descend. Distinguish reported facts from any market-impact inference, and never fill missing news from memory. "
        "For a named stock or company asking about 新闻, 消息, 资讯, 公告, 研报 or 舆情, use stock_news; default to seven calendar days and ten items. Resolve the entity to one stock, retain source, publication time and URL, distinguish media reports from formal announcements, and never fill an empty result from memory. For 最近有什么动态, 近况, 最近发生了什么 or a similarly broad named-stock update, use stock_activity instead; summarize its close-based trend, recent limit-up events, available rating context and stock news while explicitly stating missing dimensions. A named sector or industry news request is not stock_news. Public web research remains outside V1. "
        "For market-overview or sentiment questions, call market_summary but report only objective counts and rates; never assign categorical labels such as heating, divergence, cooling, risk-on or risk-off. "
        "For questions about one stock's K-line, price trend, moving averages, recent rise/fall, volume, or drawdown, call stock_kline. "
        "Historical similar-case retrieval is retired. If the user asks for similar stocks or cases, do not invent or infer matches; answer directly that this capability is unavailable and suggest score evidence, stock_kline, or tracked prediction review instead. "
        "For unavailable date/data-availability questions, do not answer directly; let backend verify local dates. "
        "Do not provide direct trading instructions, position sizing, target prices, or return promises. "
        "If the user asks for those, set safety to refuse_trade_instruction. "
        "JSON schema: {"
        "\"intent_label\": string, "
        "\"skill_name\": string|null, "
        "\"capabilities\": [string], "
        "\"context_mode\": \"standalone\"|\"entity_followup\"|\"source_refinement\", "
        "\"context_capabilities\": [string], "
        "\"safety\": \"normal\"|\"refuse_trade_instruction\", "
        "\"tool_calls\": [{\"name\": string, \"arguments\": object}], "
        "\"answer_directly\": string"
        "}."
    )


def _tool_planner_user_prompt(
    request: AgentChatRequest,
    context: "_SessionContext",
    events: list[LimitUpEvent],
) -> str:
    """Build the planner prompt from question and compact conversation context."""

    available_dates = sorted({event.trade_date for event in events}, reverse=True)
    latest_local_trade_date = available_dates[0] if available_dates else None
    context_payload = {
        "calendar_today": date.today().isoformat(),
        "latest_local_trade_date": (
            latest_local_trade_date.isoformat() if latest_local_trade_date else None
        ),
        "available_trade_dates": [
            item.isoformat() for item in available_dates[:20]
        ],
        "date_instruction": (
            "If the user provides no explicit date, or says today/latest/current, "
            "omit date arguments so backend tools use their latest available data. "
            "Never infer a historical date from conversation history. "
            "If the user asks for a date outside available_trade_dates, call no "
            "rating tool for that date and explain data is missing."
        ),
        "message": request.message,
        "backend_query_contract": (
            build_limit_up_query_contract(
                request.message,
                request_trade_date=request.trade_date,
            ).to_dict()
            if _looks_like_limit_up_event_question(request.message)
            else None
        ),
        "request_trade_date": (
            request.trade_date.isoformat() if request.trade_date else None
        ),
        "request_symbol": request.symbol,
        "page_context": request.page_context,
        "conversation_history": context.conversation_history,
        "recent_context": {
            "symbol": context.symbol,
            "trade_date": context.trade_date.isoformat() if context.trade_date else None,
            "filter": context.filter_query.label if context.filter_query else None,
            "matched_symbols": context.matched_symbols[:20],
            "last_capabilities": context.last_capabilities,
        },
    }
    return json.dumps(context_payload, ensure_ascii=False, separators=(",", ":"))


def _add_composed_tool_facts(message: str, facts: dict[str, Any]) -> None:
    """Add deterministic joins needed by questions that combine multiple tools."""

    intersection = _build_hot_stock_event_intersection(message, facts)
    if intersection is not None:
        facts["hot_stock_limit_up_intersection"] = intersection


def _build_hot_stock_event_intersection(
    message: str,
    facts: dict[str, Any],
) -> dict[str, Any] | None:
    """Join a popularity ranking with a filtered limit-up pool by stock symbol."""

    if not _looks_like_hot_stock_event_intersection_question(message):
        return None
    hot_stocks = facts.get("hot_stock_ranking")
    limit_up_events = facts.get("limit_up_events")
    if not isinstance(hot_stocks, dict) or not isinstance(limit_up_events, dict):
        return None

    events = {
        str(item.get("symbol")): item
        for item in limit_up_events.get("events", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    joined: list[dict[str, Any]] = []
    for hot_stock in hot_stocks.get("items", []):
        if not isinstance(hot_stock, dict):
            continue
        symbol = str(hot_stock.get("symbol") or "")
        event = events.get(symbol)
        if event is None:
            continue
        joined.append(
            {
                "rank": hot_stock.get("rank"),
                "symbol": symbol,
                "name": hot_stock.get("name") or event.get("name"),
                "industry": event.get("industry"),
                "board_height": event.get("board_height"),
                "board_height_text": event.get("board_height_text"),
                "first_limit_time": event.get("first_limit_time"),
                "break_count": event.get("break_count"),
                "closed_limit": event.get("closed_limit"),
            }
        )
    joined.sort(
        key=lambda item: (
            item.get("rank") if isinstance(item.get("rank"), int) else 10_000,
            item.get("symbol") or "",
        )
    )
    return {
        "source": hot_stocks.get("source"),
        "source_label": hot_stocks.get("source_label"),
        "captured_at_beijing": hot_stocks.get("captured_at_beijing"),
        "data_fresh": hot_stocks.get("data_fresh"),
        "requested_count": hot_stocks.get("requested_count"),
        "hot_stock_count": len(hot_stocks.get("items", [])),
        "trade_date": limit_up_events.get("trade_date"),
        "event_label": _hot_stock_event_label(message),
        "event_count": len(events),
        "matched_count": len(joined),
        "items": joined,
    }


def _looks_like_hot_stock_event_intersection_question(message: str) -> bool:
    """Recognize questions that filter a popularity ranking by a limit-up cohort."""

    compact = re.sub(r"\s+", "", message).lower()
    has_hot_stock_scope = any(
        term in compact
        for term in ("热股榜", "热股排行", "热门股", "人气榜", "人气排名")
    )
    has_event_filter = any(
        term in compact
        for term in ("首板", "连板", "涨停", "炸板")
    )
    asks_for_subset = any(
        term in compact
        for term in ("哪些", "有谁", "有哪", "筛出", "找出", "属于", "同时", "交集")
    )
    return has_hot_stock_scope and has_event_filter and asks_for_subset


def _hot_stock_event_label(message: str) -> str:
    """Return a user-facing label for the event-side filter."""

    if "首板" in message:
        return "首板票"
    if "连板" in message:
        return "连板票"
    if "炸板" in message:
        return "炸板票"
    return "涨停票"


def _format_capital_flow_amount(value: object) -> str | None:
    """Format a valid yuan amount for user-facing capital-flow answers."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    amount = float(value)
    if not math.isfinite(amount):
        return None
    sign = "+" if amount > 0 else ""
    if abs(amount) >= 100_000_000:
        return f"{sign}{amount / 100_000_000:.2f} 亿元"
    return f"{sign}{amount / 10_000:.2f} 万元"


def _template_answer_from_tool_facts(
    *,
    request: AgentChatRequest,
    intent: str,
    facts: dict[str, Any],
) -> str:
    """Build a useful fallback answer from tool facts when final LLM times out."""

    if _QuestionSignals.from_message(request.message).market_environment:
        return _template_market_environment_answer(facts)

    if "hot_stock_limit_up_intersection" in facts:
        payload = facts["hot_stock_limit_up_intersection"]
        items = payload.get("items", []) if isinstance(payload, dict) else []
        source_label = payload.get("source_label") or "热股"
        event_label = payload.get("event_label") or "涨停票"
        requested_count = payload.get("requested_count") or payload.get("hot_stock_count")
        lines = [
            f"{payload.get('trade_date')} {source_label}热股榜前 {requested_count} 中，"
            f"共有 {len(items)} 只{event_label}。"
        ]
        if items:
            for item in items:
                lines.append(
                    f"- 热股第 {item.get('rank')} 名 "
                    f"{item.get('name')}({item.get('symbol')})，"
                    f"行业 {item.get('industry') or '暂无'}，"
                    f"首封 {item.get('first_limit_time') or '未知'}，"
                    f"炸板 {item.get('break_count')} 次。"
                )
        else:
            lines.append(f"当前榜单与当日{event_label}名单没有交集。")
        lines.append(
            f"口径：热股榜返回 {payload.get('hot_stock_count')} 只，"
            f"当日{event_label}池 {payload.get('event_count')} 只，按股票代码求交集。"
        )
        lines.append("热度只反映市场关注度，不代表后续上涨概率。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "remote_limit_up_pool" in facts:
        payload = facts["remote_limit_up_pool"]
        items = payload.get("items", []) if isinstance(payload, dict) else []
        lines = [
            f"同花顺涨停池查询：上游共 {payload.get('upstream_total')} 只，"
            f"当前条件命中 {len(items)} 只。"
        ]
        display_items = items if _looks_like_exhaustive_list_request(request.message) else items[:12]
        for item in display_items:
            lines.append(
                f"- {item.get('name')}({item.get('symbol')}) "
                f"{item.get('board_height_text') or str(item.get('board_height')) + '板'}，"
                f"封板 {item.get('limit_up_time') or '未知'}，"
                f"原因：{item.get('limit_up_reason') or '未提供'}。"
            )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "hot_stock_ranking" in facts:
        payload = facts["hot_stock_ranking"]
        source_label = payload.get("source_label") or payload.get("source") or "热股"
        lines = [
            f"{source_label}热股榜快照时间 {payload.get('captured_at_beijing') or payload.get('captured_at')}"
            f"（北京时间），返回 {payload.get('count')}/{payload.get('requested_count')} 只："
        ]
        requested_limit = extract_result_limit(request.message) or 20
        for item in payload.get("items", [])[:requested_limit]:
            change = item.get("rank_change")
            change_text = f"，排名变化 {change:+d}" if isinstance(change, int) else ""
            heat = item.get("heat")
            heat_text = f"，热度 {heat}" if isinstance(heat, int) else ""
            lines.append(
                f"- 第 {item.get('rank')} 名 {item.get('name')}({item.get('symbol')})"
                f"{heat_text}{change_text}。"
            )
        if not payload.get("complete"):
            lines.append(
                f"该数据源当前只提供 {payload.get('count')} 条，"
                f"未达到请求的 {payload.get('requested_count')} 条。"
            )
        lines.append("人气排名反映关注度和拥挤程度，不等同于上涨概率。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "dragon_tiger_list" in facts:
        payload = facts["dragon_tiger_list"]
        lines = [
            f"{payload.get('trade_date') or '最新'} 同花顺龙虎榜命中 "
            f"{payload.get('matched_count')} 条："
        ]
        for item in payload.get("items", [])[:20]:
            flow_parts = []
            for label, key in (
                ("净买额", "net_buy_amount"),
                ("机构净买", "organization_net_buy_amount"),
                ("游资净买", "hot_money_net_buy_amount"),
            ):
                amount_text = _format_capital_flow_amount(item.get(key))
                if amount_text is not None:
                    flow_parts.append(f"{label} {amount_text}")
            if not flow_parts:
                continue
            lines.append(
                f"- {item.get('name')}({item.get('symbol')})，"
                f"{'，'.join(flow_parts)}。"
            )
        if len(lines) == 1:
            lines.append("当前榜单没有可展示的资金净流数据。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "prediction_quality_audit" in facts:
        audit = facts["prediction_quality_audit"]
        status = audit.get("policy_status") or {}
        benchmarks = audit.get("benchmarks") or []
        current = next(
            (
                item
                for item in benchmarks
                if item.get("benchmark") == "audited_policy_top_k"
            ),
            {},
        )
        early = next(
            (
                item
                for item in benchmarks
                if item.get("benchmark") == "early_seal_top_k"
            ),
            {},
        )

        def metric(item: dict[str, Any], key: str) -> str:
            value = item.get(key)
            return "暂无" if value is None else f"{float(value):.2f}%"

        coverage = float(audit.get("next_day_outcome_coverage_rate") or 0)
        ready_dates = int(status.get("outcome_ready_trade_dates") or 0)
        required_dates = int(status.get("required_trade_dates") or 60)
        lines = [
            f"{audit.get('start_date')} 至 {audit.get('end_date')} 的预测质量审计：",
            f"本次审计版本为 {audit.get('audited_scoring_version')}，"
            f"去重后 {audit.get('canonical_prediction_count')} 条预测；"
            f"成熟预测日 {audit.get('next_day_mature_trade_date_count')} 个，"
            f"Top10 结果完整日 {audit.get('complete_next_day_trade_date_count')} 个，"
            f"次日 Outcome 覆盖率 {coverage:.1%}。",
            f"当前评分 Top10 次日开盘到收盘均值 "
            f"{metric(current, 'avg_next_open_to_close_pct')}，"
            f"最早封板基线为 {metric(early, 'avg_next_open_to_close_pct')}。",
            f"评分 v3 目前有 {ready_dates}/{required_dates} 个结果日，"
            f"状态为{'满足晋级门槛' if status.get('promotion_eligible') else '影子验证中'}。",
        ]
        gate_reasons = status.get("gate_reasons") or []
        if gate_reasons:
            reasons = [
                str(item).rstrip("。；;") for item in gate_reasons[:4]
            ]
            lines.append("未晋级原因：" + "；".join(reasons) + "。")
        lines.extend(str(item) for item in (audit.get("recommendations") or [])[:2])
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "market_index_trend" in facts:
        trend = facts["market_index_trend"]
        lines = [
            f"截至 {trend.get('data_as_of')}，近 {trend.get('requested_days')} 个交易日"
            "主要指数表现如下："
        ]
        for item in trend.get("indices", []):
            lines.append(
                f"- {item.get('name')}：{item.get('start_close')} → "
                f"{item.get('end_close')}，区间涨跌 "
                f"{float(item.get('return_pct') or 0):+.2f}%；"
                f"上涨 {item.get('positive_days')} 日、下跌 "
                f"{item.get('negative_days')} 日，最大收盘回撤 "
                f"{float(item.get('max_drawdown_pct') or 0):.2f}%。"
            )
        if not trend.get("data_fresh"):
            lines.append(
                f"请求截止日为 {trend.get('requested_end_date')}，"
                "以上按最近可用交易日展示。"
            )
        lines.append("以上只描述指数已经发生的区间走势，不代表后续方向。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "sector_performance" in facts:
        sector = facts["sector_performance"]
        if sector.get("sector_name"):
            lines = [
                f"{sector.get('data_as_of')} {sector.get('sector_name')}板块涨跌幅 "
                f"{sector.get('change_pct')}%，行业排名 "
                f"{sector.get('rank')}/{sector.get('sector_count')}。",
                f"上涨 {sector.get('up_count')} 家，下跌 {sector.get('down_count')} 家，"
                f"成交额 {sector.get('amount_yi')} 亿元，资金净流入 "
                f"{sector.get('net_inflow_yi')} 亿元。",
                f"领涨股为 {sector.get('leader_name')}，涨跌幅 "
                f"{sector.get('leader_change_pct')}%。",
            ]
            if not sector.get("data_fresh"):
                lines.append("板块历史数据未到请求日期，以上按最近可用交易日展示。")
        else:
            lines = [f"{sector.get('data_as_of')} 行业板块涨幅靠前："]
            lines.extend(
                f"- {item.get('sector_name')}：{item.get('change_pct')}%"
                for item in sector.get("top_sectors", [])
            )
        if "web_search" in facts:
            lines.append("相关公开信息：")
            lines.extend(
                f"- {item.get('title')}：{item.get('url')}"
                for item in facts["web_search"].get("results", [])[:3]
            )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "daily_board_promotion" in facts:
        return _template_daily_board_promotion_answer(facts["daily_board_promotion"])

    if "first_board_discovery" in facts:
        discovery = facts["first_board_discovery"]
        auction_final = discovery.get("auction_final") or {}
        final_candidates = auction_final.get("candidates") or []
        if final_candidates:
            lines = [
                f"{auction_final.get('trade_date')} 首板挖掘 09:25 竞价终选如下："
            ]
            lines.extend(
                f"{index}. {item.get('name')}({item.get('symbol')}) "
                f"终选 {item.get('final_score')} 分，竞价涨幅 "
                f"{item.get('auction_pct')}%，竞价量比 {item.get('auction_volume_ratio')}。"
                for index, item in enumerate(final_candidates[:10], start=1)
            )
            lines.append("该名单为竞价终值确认后的研究排序，不代表涨停概率。")
            lines.append(TEXT["safety"])
            return "\n".join(lines)
        candidates = discovery.get("candidates", [])
        target = discovery.get("target_trade_date") or "下一交易日"
        lines = [
            f"基于 {discovery.get('data_as_of')} 收盘数据，{target} 首板挖掘观察池如下："
        ]
        for index, item in enumerate(candidates[:10], start=1):
            themes = item.get("themes") or []
            primary_theme = themes[0] if themes else {}
            catalysts = item.get("news_catalysts") or []
            live = item.get("latest_intelligence") or {}
            lines.append(
                f"{index}. {item.get('name')}({item.get('symbol')}) "
                f"{item.get('score')}分/{item.get('rating')}，"
                f"题材 {primary_theme.get('name', '暂无')}"
                f"({primary_theme.get('change_pct', 0):+.1f}%)，"
                f"{item.get('pattern_label')}，量比 {item.get('volume_ratio_5d')}。"
            )
            if live and live.get("change_pct") is not None:
                lines.append(
                    f"   最新情报：{live.get('change_pct')}%，"
                    f"更新于 {str(live.get('refreshed_at', ''))[:16]}。"
                )
            if catalysts:
                lines.append(f"   催化：{catalysts[0]}")
        if not candidates:
            lines.append("当前没有满足数据与流动性要求的候选。")
        lines.append("该名单先按热门题材和新闻催化圈选，再按量价结构排序，不代表涨停概率。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "first_board_ratings" in facts and "limit_up_events" not in facts:
        ratings = facts["first_board_ratings"]
        auction_final = ratings.get("auction_final") or {}
        final_candidates = auction_final.get("candidates") or []
        if final_candidates:
            lines = [
                f"{auction_final.get('trade_date')} 一进二接力 09:25 竞价终选如下："
            ]
            lines.extend(
                f"{index}. {item.get('name')}({item.get('symbol')}) "
                f"终选 {item.get('final_score')} 分，竞价涨幅 "
                f"{item.get('auction_pct')}%，竞价量比 {item.get('auction_volume_ratio')}。"
                for index, item in enumerate(final_candidates[:10], start=1)
            )
            lines.append(TEXT["safety"])
            return "\n".join(lines)
        if _looks_like_first_board_position_question(request.message):
            return _template_first_board_position_answer(ratings)
        candidates = (
            ratings.get("candidates") or ratings.get("top_candidates") or []
            if isinstance(ratings, dict)
            else []
        )
        lines = [f"{ratings.get('trade_date')} 首板候选评分靠前的股票如下："]
        for index, item in enumerate(candidates[:8], start=1):
            fact = item.get("facts") or item if isinstance(item, dict) else {}
            live = item.get("latest_intelligence") or {}
            lines.append(
                f"{index}. {fact.get('name')}({fact.get('symbol')}) "
                f"{item.get('score')}分/{item.get('rating')}，"
                f"行业 {fact.get('industry')}，首封 {str(fact.get('first_limit_time', ''))[:5]}，"
                f"炸板 {fact.get('break_count')} 次。"
            )
            if live and live.get("change_pct") is not None:
                lines.append(
                    f"   最新情报：{live.get('change_pct')}%，"
                    f"更新于 {str(live.get('refreshed_at', ''))[:16]}。"
                )
        if not candidates:
            lines.append("当前没有满足过滤条件的首板候选。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "limit_up_events" in facts:
        payload = facts["limit_up_events"]
        events = payload.get("events", []) if isinstance(payload, dict) else []
        lines = [f"{payload.get('trade_date')} 查询到 {len(events)} 条匹配涨停事件："]
        display_events = (
            events
            if _looks_like_exhaustive_list_request(request.message)
            else events[:12]
        )
        for item in display_events:
            lines.append(
                f"- {item.get('name')}({item.get('symbol')}) "
                f"{item.get('board_height_text') or str(item.get('board_height')) + '板'}，"
                f"行业 {item.get('industry')}，炸板 {item.get('break_count')} 次。"
            )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "review_high_score_picks" in facts:
        review = facts["review_high_score_picks"]
        high_score_promotion_question = _looks_like_high_score_promotion_question(
            request.message
        )
        lines = [f"{review.get('start_date')} 至 {review.get('end_date')} 高分首板复盘："]
        if not high_score_promotion_question:
            lines.append(
                f"样本 {review.get('sample_size')} 只，成功 {review.get('success_count')}，"
                f"失败 {review.get('failed_count')}，待观察 {review.get('pending_count')}。"
            )
        top_rate = review.get("top_pick_promotion_rate")
        market_rate = review.get("market_promotion_rate")
        promotion_delta = review.get("promotion_rate_delta")
        if top_rate is not None and market_rate is not None:
            lines.append(
                f"最近 {review.get('promotion_ready_date_count')} 个结果完整交易日，"
                f"每日评分 Top10 的1进2成功率为 "
                f"{review.get('top_pick_promoted_count')}/"
                f"{review.get('top_pick_promotion_sample_size')}"
                f"（{float(top_rate):.1%}）；同期全部首板为 "
                f"{review.get('market_promoted_count')}/"
                f"{review.get('market_promotion_sample_size')}"
                f"（{float(market_rate):.1%}），"
                f"相差 {float(promotion_delta or 0) * 100:+.1f} 个百分点。"
            )
            ready_comparisons = [
                item
                for item in (review.get("promotion_comparisons") or [])
                if item.get("outcome_ready")
            ][-5:]
            for item in ready_comparisons:
                lines.append(
                    f"- {item.get('trade_date')}→{item.get('next_trade_date')}："
                    f"Top10 {item.get('top_pick_promoted_count')}/"
                    f"{item.get('top_pick_sample_size')}"
                    f"（{float(item.get('top_pick_promotion_rate') or 0):.1%}），"
                    f"全部首板 {item.get('market_promoted_count')}/"
                    f"{item.get('market_first_board_sample_size')}"
                    f"（{float(item.get('market_promotion_rate') or 0):.1%}）。"
                )
        if high_score_promotion_question:
            lines.append(
                "以上按预测日收盘后的评分 Top10 统计，并与同日全部收盘首板使用同一晋级口径；"
                "当前仅有近期小样本，不能据此认定评分已稳定提升一进二能力。"
            )
            lines.append(TEXT["safety"])
            return "\n".join(lines)
        for title, key in (
            ("主要发现", "main_findings"),
            ("成功共性", "successful_patterns"),
            ("失败共性", "failed_patterns"),
            ("审美调整", "adjustment_suggestions"),
        ):
            values = review.get(key) or []
            if values:
                lines.append(f"{title}：{'; '.join(str(item) for item in values[:3])}")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "scoring_policy_status" in facts:
        payload = facts["scoring_policy_status"]
        champion = payload.get("champion") or {}
        latest = payload.get("latest_optimization") or {}
        challenger = latest.get("challenger_policy") or {}
        comparison = latest.get("comparison") or {}
        gate_reasons = comparison.get("gate_reasons") or []
        lines = [
            f"当前线上评分策略是 Champion：{champion.get('version')}，"
            f"来源为 {champion.get('source')}。"
        ]
        if challenger:
            lines.append(
                f"最近生成的 Challenger 是 {challenger.get('version')}；"
                f"样本外晋级资格：{'通过' if comparison.get('promotion_eligible') else '未通过'}；"
                f"实际启用：{'是' if latest.get('activated') else '否'}。"
            )
            if gate_reasons:
                lines.append("门槛检查：" + "；".join(str(item) for item in gate_reasons[:5]))
        else:
            lines.append("目前还没有完成一次可用的 Challenger 样本外优化。")
        lines.append(
            "系统会自动生成候选权重并做时间顺序样本外评估，但默认只以影子模式注册，"
            "未通过门槛且未经显式启用时不会替换线上 Champion。"
        )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "stock_activity" in facts:
        activity = facts["stock_activity"]
        lines = [
            f"{activity.get('name')}({activity.get('symbol')}) 近期动态，"
            f"收盘数据截至 {activity.get('data_as_of') or '暂无'}："
        ]
        kline = activity.get("kline") or {}
        if kline:
            lines.append(
                f"- 走势：最近 {kline.get('requested_days')} 个交易日为 "
                f"{kline.get('trend')}，5日涨跌 {kline.get('return_5d_pct')}%，"
                f"20日涨跌 {kline.get('return_20d_pct')}%，量比 "
                f"{kline.get('volume_ratio_5d')}。"
            )
        events = activity.get("recent_limit_up_events") or []
        if events:
            lines.append("- 近期涨停记录：")
            for item in events[:5]:
                lines.append(
                    f"  - {item.get('trade_date')} {item.get('board_height')}板，"
                    f"首封 {item.get('first_limit_time')}，炸板 {item.get('break_count')} 次。"
                )
        context = activity.get("rating_context") or {}
        context_parts = []
        if context.get("popularity_rank") is not None:
            context_parts.append(f"人气排名 {context.get('popularity_rank')}")
        if context.get("dragon_tiger_on_list"):
            context_parts.append("该评分日有龙虎榜记录")
        if context.get("float_market_cap") is not None:
            context_parts.append(
                f"流通市值 {float(context.get('float_market_cap')) / 100_000_000:.2f} 亿元"
            )
        if context_parts:
            lines.append("- 评分补充事实：" + "，".join(context_parts) + "。")
        news = activity.get("news") or {}
        if news.get("items"):
            lines.append(
                f"- 最近 {news.get('window_days')} 天个股资讯"
                f"（{news.get('cache_status')}）："
            )
            for item in news.get("items", [])[:8]:
                published_at = str(item.get("published_at") or "").replace("T", " ")[:16]
                lines.append(
                    f"  - {published_at} {item.get('source')}：{item.get('title')} "
                    f"{item.get('url')}"
                )
        missing = activity.get("data_missing") or []
        if missing:
            lines.append("- 数据限制：" + "；".join(str(item) for item in missing[:4]))
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "stock_news" in facts:
        news = facts["stock_news"]
        fetched_at = str(news.get("fetched_at") or "").replace("T", " ")[:16]
        lines = [
            f"{news.get('name')}({news.get('symbol')}) 最近 {news.get('window_days')} 天资讯，"
            f"抓取时间 {fetched_at}，缓存状态 {news.get('cache_status')}："
        ]
        type_labels = {
            "news": "新闻",
            "announcement_report": "公告类报道",
            "research": "研究资讯",
            "regulatory": "监管信息",
        }
        for item in news.get("items", [])[:20]:
            published_at = str(item.get("published_at") or "").replace("T", " ")[:16]
            lines.append(
                f"- [{type_labels.get(item.get('item_type'), '资讯')}] {published_at} "
                f"{item.get('source')}：{item.get('title')}。"
                f"{item.get('summary') or ''} {item.get('url')}"
            )
        if not news.get("items"):
            lines.append("该时间窗口内没有获取到与这只股票直接相关的资讯。")
        if news.get("data_missing"):
            lines.append("数据限制：" + "；".join(news.get("data_missing")[:3]))
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "stock_kline" in facts:
        kline = facts["stock_kline"]
        trend_label = {
            "rising": "偏强上行",
            "falling": "偏弱下行",
            "oscillating": "震荡",
            "insufficient": "样本不足",
        }.get(kline.get("trend"), str(kline.get("trend")))
        freshness = "已到指定交易日" if kline.get("data_fresh") else "数据尚未到指定交易日"
        return (
            f"{kline.get('symbol')} 最近 {kline.get('requested_days')} 个交易日走势为{trend_label}，"
            f"截至 {kline.get('data_as_of')} 收盘 {kline.get('latest_close')}。"
            f"5日涨跌 {kline.get('return_5d_pct')}%，10日涨跌 {kline.get('return_10d_pct')}%，"
            f"20日涨跌 {kline.get('return_20d_pct')}%，区间最大回撤 {kline.get('max_drawdown_pct')}%。"
            f"数据状态：{freshness}。\n{TEXT['safety']}"
        )

    if "rating_evaluation" in facts:
        evaluation = facts["rating_evaluation"]
        lines = [
            f"{evaluation.get('start_date')} 至 {evaluation.get('end_date')} 评分复盘："
            f"共 {evaluation.get('prediction_count')} 条预测，"
            f"{evaluation.get('outcome_ready_count')} 条已有次日开盘介入结果。",
        ]
        for item in evaluation.get("summary", [])[:4]:
            lines.append(f"- {item}")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "market_summary" in facts:
        summary = facts["market_summary"]
        return (
            f"{summary.get('trade_date')} 本地数据显示，涨停 {summary.get('limit_up_count')} 只，"
            f"首板 {summary.get('first_board_count')} 只，连板 {summary.get('continued_board_count')} 只，"
            f"炸板率 {summary.get('failed_limit_up_rate')}，最高连板 {summary.get('max_board_height')} 板。\n"
            f"{TEXT['safety']}"
        )

    if "finance_news" in facts:
        news = facts["finance_news"]
        fetched_at = str(news.get("fetched_at") or "").replace("T", " ")[:16]
        sources = "、".join(news.get("sources") or []) or "财经数据源"
        lines = [
            f"截至 {fetched_at}（北京时间），{sources} 近 "
            f"{news.get('window_hours')} 小时财经快讯中，较值得关注的有："
        ]
        for item in news.get("items", [])[:8]:
            published_at = str(item.get("published_at") or "").replace("T", " ")[:16]
            summary = item.get("summary") or "暂无摘要"
            lines.append(
                f"- [{item.get('category')}] {published_at} {item.get('source')}："
                f"{item.get('title')}。{summary} {item.get('url')}"
            )
        if not news.get("items"):
            lines.append("当前时间窗口内没有获取到可用财经快讯。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "web_search" in facts:
        search = facts["web_search"]
        lines = [f"公开网络检索“{search.get('query')}”得到以下相关结果："]
        for item in search.get("results", [])[:8]:
            lines.append(
                f"- {item.get('title')}（{item.get('domain')}）："
                f"{item.get('snippet')} {item.get('url')}"
            )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    return UNANSWERABLE_TEXT


def _template_market_environment_answer(facts: dict[str, Any]) -> str:
    """Render a complete four-part market review when the final LLM is unavailable."""

    summary = facts.get("market_summary")
    trend = facts.get("market_index_trend")
    sectors = facts.get("sector_performance")
    popularity = facts.get("hot_stock_ranking")
    summary = summary if isinstance(summary, dict) else {}
    trend = trend if isinstance(trend, dict) else {}
    sectors = sectors if isinstance(sectors, dict) else {}
    popularity = popularity if isinstance(popularity, dict) else {}

    lines = [f"截至 {summary.get('trade_date') or trend.get('data_as_of') or '最新可用交易日'} 的市场环境综述："]
    lines.append("\n### 大盘指数")
    indices = trend.get("indices") or []
    if indices:
        for item in indices:
            points = item.get("points") or []
            latest_change = points[-1].get("change_pct") if points else None
            latest_text = (
                f"最新交易日 {float(latest_change):+.2f}%"
                if isinstance(latest_change, (int, float))
                else "最新交易日涨跌暂缺"
            )
            lines.append(
                f"- {item.get('name')}：{latest_text}，近 "
                f"{trend.get('requested_days')} 个交易日 "
                f"{float(item.get('return_pct') or 0):+.2f}%，最大回撤 "
                f"{float(item.get('max_drawdown_pct') or 0):.2f}%。"
            )
    else:
        lines.append("- 指数走势数据暂时无法获取。")

    lines.append("\n### 涨跌停结构")
    if summary:
        down_text = (
            f"跌停 {summary.get('limit_down_count')} 只"
            if summary.get("limit_down_count") is not None
            else "跌停数量暂缺"
        )
        lines.append(
            f"- 涨停 {summary.get('limit_up_count')} 只，其中首板 "
            f"{summary.get('first_board_count')} 只、连板 "
            f"{summary.get('continued_board_count')} 只；未回封 "
            f"{summary.get('unsealed_count')} 只，{down_text}，最高 "
            f"{summary.get('max_board_height')} 板。"
        )
    else:
        lines.append("- 涨跌停结构数据暂时无法获取。")

    lines.append("\n### 板块强弱")
    top_sectors = sectors.get("top_sectors") or []
    bottom_sectors = sectors.get("bottom_sectors") or []
    if top_sectors:
        lines.append("- 涨幅靠前：" + "；".join(_format_sector_row(item) for item in top_sectors[:5]) + "。")
        lines.append("- 跌幅靠前：" + "；".join(_format_sector_row(item) for item in bottom_sectors[:5]) + "。")
    else:
        lines.append("- 行业强弱榜暂时无法获取。")

    lines.append("\n### 热门个股")
    hot_items = popularity.get("items") or []
    if hot_items:
        for item in hot_items[:5]:
            performance = (
                f"，最新涨跌 {float(item.get('change_pct')):+.2f}%"
                if isinstance(item.get("change_pct"), (int, float))
                else "，最新涨跌暂缺"
            )
            lines.append(
                f"- 人气第 {item.get('rank')} 名 "
                f"{item.get('name')}({item.get('symbol')}){performance}。"
            )
    else:
        lines.append("- 热门个股数据暂时无法获取。")

    cutoff_parts = []
    if trend.get("data_as_of"):
        cutoff_parts.append(f"指数截至 {trend.get('data_as_of')}")
    if sectors.get("data_as_of"):
        cutoff_parts.append(f"板块截至 {sectors.get('data_as_of')}")
    if popularity.get("captured_at_beijing"):
        cutoff_parts.append(f"人气榜抓取于 {popularity.get('captured_at_beijing')}（北京时间）")
    if cutoff_parts:
        lines.append("\n数据口径：" + "；".join(cutoff_parts) + "。")
    lines.append("以上按客观数据拆分展示，不使用单一情绪标签代替事实。")
    lines.append(TEXT["safety"])
    return "\n".join(lines)


def _format_sector_row(item: dict[str, Any]) -> str:
    """Format one sector ranking row without exposing missing placeholders."""

    leader = (
        f"，领涨 {item.get('leader_name')}"
        if item.get("leader_name")
        else ""
    )
    return (
        f"{item.get('sector_name')} "
        f"{float(item.get('change_pct') or 0):+.2f}%{leader}"
    )


def _tool_answer_system_prompt(
    *,
    agent_profile: str,
    exhaustive_event_answer: bool = False,
    complete_position_answer: bool = False,
    complete_hot_stock_answer: bool = False,
    hot_stock_event_intersection_answer: bool = False,
    market_environment_answer: bool = False,
    skill_instruction: str = "",
) -> str:
    """Instruct the LLM to answer only from executed tool facts."""

    profile_instruction = (
        " Extended preview tools may include current external evidence; state each "
        "source and capture time without treating popularity or news as prediction."
        if agent_profile == EXTENDED_AGENT_PROFILE
        else (
            " V1 primarily uses completed close and stored historical facts. For a "
            "hot_stock_ranking result, state its source, Beijing capture time and "
            "data_fresh status instead of a close-data cutoff, and explain that "
            "popularity is attention rather than a recommendation. For finance_news, "
            "state the Beijing retrieval time plus every item's publication time, source "
            "and URL, and never invent an item or omit that external-news cutoff. For "
            "stock_news and stock_activity, retain the resolved stock, source timestamps "
            "and cache cutoff. For sector_performance, state its source, data_as_of and freshness. For other "
            "factual answers, begin with the relevant YYYY-MM-DD close-data cutoff. Never "
            "imply that a result contains intraday prices or auction data. If the question "
            "requires live limit-up verification or arbitrary public-web evidence, output "
            "exactly '抱歉，该问题无法回答'."
        )
    )
    exhaustive_instruction = (
        " EXHAUSTIVE_LIST_OUTPUT: The user explicitly requested every matched event. "
        "Include every item from limit_up_events.events exactly once, preferably as compact "
        "numbered lines with name and symbol; do not replace items with analysis or stop early."
        if exhaustive_event_answer
        else ""
    )
    position_instruction = (
        " POSITION_CLASSIFICATION_OUTPUT: Position means the pre-board K-line regime in "
        "first_board_ratings.position_classification, never first seal time. State that the "
        "scope is the rated candidate pool, include every position group and every candidate "
        "exactly once, and mention missing position data separately."
        if complete_position_answer
        else ""
    )
    hot_stock_instruction = (
        " COMPLETE_HOT_STOCK_OUTPUT: The user explicitly requested a Top-N popularity "
        "ranking. Include every item from hot_stock_ranking.items up to requested_count "
        "exactly once as compact numbered lines with rank, name and symbol; do not stop early."
        if complete_hot_stock_answer
        else ""
    )
    intersection_instruction = (
        " SET_INTERSECTION_OUTPUT: The question asks which popularity-ranked stocks also "
        "match a limit-up event filter. Use hot_stock_limit_up_intersection as the joined "
        "result, list every intersection item exactly once in popularity-rank order, and "
        "do not output non-matching popularity rows or the two source lists separately."
        if hot_stock_event_intersection_answer
        else ""
    )
    market_environment_instruction = (
        " MARKET_ENVIRONMENT_OUTPUT: Produce a substantive four-section review, not a "
        "brief sentiment label. Section 1 covers all major indices with latest-day change "
        "and 5-day return. Section 2 covers limit-up, first-board, continued-board, "
        "unsealed, limit-down and maximum-board facts, explicitly stating unavailable "
        "fields. Section 3 lists both the five strongest and five weakest industries with "
        "change percentages and leaders when available. Section 4 lists the five most "
        "popular stocks with popularity rank and latest quote change when available. End "
        "with a short objective synthesis based only on those facts, without a categorical "
        "market-sentiment label. State every distinct data cutoff because close, sector and "
        "popularity snapshots may differ."
        if market_environment_answer
        else ""
    )
    return (
        "You are LimitUpLab's A-share first-board research agent. "
        "Answer in Chinese using only the executed tool facts. "
        f"{profile_instruction} "
        "For prediction evaluation, prioritize next_open_to_close_pct and entry-open drawdown; "
        "treat promotion and intraday highs as separate facts rather than success labels. "
        "For stock trend questions, cite stock_kline.data_as_of and data_fresh, and base the description on returns, moving averages, volume and drawdown. "
        "For stock_news, state the resolved name and symbol, retrieval time, calendar-day window and cache status; list publication time, source, item type, title, concise summary and URL, and do not call a media report a formal announcement. For stock_activity, separate already observed close/K-line facts, historical limit-up events, rating context and timestamped news; explicitly mention unavailable dimensions and never imply intraday monitoring. "
        "For broad-index trend questions, cite the requested window and data_as_of, compare all returned major indices using period returns, up/down days and drawdown, and do not substitute limit-up counts for index performance. "
        "Do not assign categorical market-sentiment labels such as heating, divergence, cooling, risk-on or risk-off; report objective market counts, rates and index changes instead. "
        "For daily_board_promotion, treat each trade_date as the day promotion was observed from previous_trade_date; report empirical sample counts with every rate and distinguish all limit-up stocks, first-board-to-second-board, and existing continued-board cohorts. "
        "For first_board_discovery and first_board_ratings, use auction_final when it is present because it is the current session's immutable 09:25 final ranking; otherwise use the close-data initial ranking. State which stage and date are being reported, and never convert either score into a claimed limit-up probability. For a close-data first_board_discovery result, explain that candidates had not reached limit-up on the cutoff date. "
        "For review_high_score_picks promotion comparisons, report Top10 and full-market first-board sample counts together, separate pending dates, and express promotion_rate_delta as percentage points. "
        "For dragon_tiger_list, omit every missing capital-flow field and format each valid CNY amount as signed 亿元 or 万元; never expose raw yuan values, None, null, NaN, or a missing-data placeholder. "
        "Historical similar-case retrieval is retired; never invent or infer a similar stock or case from the available facts. "
        "When mentioning dates, include ISO format YYYY-MM-DD even if also using Chinese date wording. "
        "If the facts do not directly and sufficiently support the question, output exactly "
        "'抱歉，该问题无法回答' and nothing else. Never expose internal tool names, function names, fact keys, "
        "planner details, schemas, or implementation identifiers to the user. Describe "
        "evidence in business language such as local market data, promotion statistics, "
        "K-line data, or public information, without saying which internal tool produced it. "
        "Keep the answer concise, structured, and useful. "
        "Do not provide direct trading instructions, position sizing, target prices, "
        "or return promises."
        f"{exhaustive_instruction}{position_instruction}{hot_stock_instruction}"
        f"{intersection_instruction}{market_environment_instruction}{skill_instruction}"
    )


def _tool_answer_user_prompt(
    request: AgentChatRequest,
    tool_plan: dict[str, Any],
    facts: dict[str, Any],
    context: "_SessionContext",
) -> str:
    """Build the final answer prompt from question, plan and tool outputs."""

    payload = {
        "user_question": request.message,
        "conversation_history": context.conversation_history,
        "intent": tool_plan.get("intent_label"),
        "executed_tool_facts": facts,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _looks_like_exhaustive_list_request(message: str) -> bool:
    """Return whether the user explicitly asks for the complete result set."""

    return looks_like_exhaustive_request(message)


def _requires_exhaustive_event_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Enable full-list output only for a multi-item limit-up event result."""

    if "hot_stock_limit_up_intersection" in facts:
        return False
    events = _limit_up_items_from_facts(facts)
    return (
        _looks_like_exhaustive_list_request(message)
        and len(events) > 8
    )


def _contains_every_event_symbol(answer: str, facts: dict[str, Any]) -> bool:
    """Verify an exhaustive LLM answer did not truncate or omit event rows."""

    events = _limit_up_items_from_facts(facts)
    symbols = [
        str(item.get("symbol"))
        for item in events
        if isinstance(item, dict) and item.get("symbol")
    ]
    return bool(symbols) and all(symbol in answer for symbol in symbols)


def _requires_complete_hot_stock_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether a requested Top-N ranking is fully available for rendering."""

    requested = extract_result_limit(message)
    payload = facts.get("hot_stock_ranking")
    return (
        "hot_stock_limit_up_intersection" not in facts
        and requested is not None
        and requested > 20
        and isinstance(payload, dict)
        and bool(payload.get("complete"))
        and len(payload.get("items") or []) >= requested
    )


def _contains_every_hot_stock_symbol(answer: str, facts: dict[str, Any]) -> bool:
    """Verify that a full Top-N answer preserves every ranked stock."""

    payload = facts.get("hot_stock_ranking")
    if not isinstance(payload, dict):
        return False
    requested = int(payload.get("requested_count") or 0)
    symbols = [
        str(item.get("symbol"))
        for item in (payload.get("items") or [])[:requested]
        if isinstance(item, dict) and item.get("symbol")
    ]
    return bool(symbols) and all(symbol in answer for symbol in symbols)


def _requires_hot_stock_event_intersection_answer(facts: dict[str, Any]) -> bool:
    """Return whether two executed stock lists were composed as a set intersection."""

    return isinstance(facts.get("hot_stock_limit_up_intersection"), dict)


def _contains_exact_hot_stock_event_intersection(
    answer: str,
    facts: dict[str, Any],
) -> bool:
    """Reject answers that omit joined rows or leak non-matching popularity rows."""

    intersection = facts.get("hot_stock_limit_up_intersection")
    hot_stocks = facts.get("hot_stock_ranking")
    if not isinstance(intersection, dict) or not isinstance(hot_stocks, dict):
        return False
    matched_symbols = {
        str(item.get("symbol"))
        for item in intersection.get("items", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    if not matched_symbols:
        return any(term in answer for term in ("没有交集", "暂无", "0只", "0 只"))
    non_matching_symbols = {
        str(item.get("symbol"))
        for item in hot_stocks.get("items", [])
        if isinstance(item, dict)
        and item.get("symbol")
        and str(item.get("symbol")) not in matched_symbols
    }
    return (
        all(symbol in answer for symbol in matched_symbols)
        and not any(symbol in answer for symbol in non_matching_symbols)
    )


def _requires_complete_position_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether the response must preserve the full position grouping."""

    ratings = facts.get("first_board_ratings")
    classification = (
        ratings.get("position_classification") if isinstance(ratings, dict) else None
    )
    return (
        _looks_like_first_board_position_question(message)
        and isinstance(classification, dict)
        and bool(classification.get("groups") or classification.get("missing_candidates"))
    )


def _contains_complete_position_groups(answer: str, facts: dict[str, Any]) -> bool:
    """Check that an LLM position answer retains every group and candidate."""

    ratings = facts.get("first_board_ratings")
    if not isinstance(ratings, dict):
        return False
    classification = ratings.get("position_classification")
    if not isinstance(classification, dict):
        return False
    groups = classification.get("groups") or []
    labels = [
        str(group.get("label"))
        for group in groups
        if isinstance(group, dict) and group.get("label")
    ]
    symbols = [
        str(candidate.get("symbol"))
        for group in groups
        if isinstance(group, dict)
        for candidate in group.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("symbol")
    ]
    symbols.extend(
        str(candidate.get("symbol"))
        for candidate in classification.get("missing_candidates") or []
        if isinstance(candidate, dict) and candidate.get("symbol")
    )
    return bool(labels or symbols) and all(label in answer for label in labels) and all(
        symbol in answer for symbol in symbols
    )


def _requires_daily_promotion_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether the answer must preserve daily promotion observations."""

    payload = facts.get("daily_board_promotion")
    return (
        _looks_like_daily_board_promotion_question(message)
        and isinstance(payload, dict)
        and bool(payload.get("items"))
    )


def _contains_daily_promotion_facts(
    answer: str,
    facts: dict[str, Any],
    message: str,
) -> bool:
    """Check that an LLM answer includes every requested day and cohort size."""

    payload = facts.get("daily_board_promotion")
    if not isinstance(payload, dict):
        return False
    items = payload.get("items") or []
    required_tokens: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        required_tokens.extend(
            (
                str(item.get("trade_date")),
                f"{item.get('promoted_count')}/{item.get('sample_size')}",
                (
                    f"{item.get('first_board_promoted_count')}"
                    f"/{item.get('first_board_sample_size')}"
                ),
            )
        )
    if _asks_for_promoted_stock_details(message):
        latest = items[-1] if items else {}
        required_tokens.extend(
            str(stock.get("symbol"))
            for stock in latest.get("promoted_stocks") or []
            if isinstance(stock, dict)
        )
    return bool(required_tokens) and all(token in answer for token in required_tokens)


def _asks_for_promoted_stock_details(message: str) -> bool:
    """Return whether the user asks for the concrete promoted-stock list."""

    return "晋级" in message and any(
        term in message
        for term in ("哪些票", "哪些股票", "哪些个股", "股票有哪些", "票有哪些")
    )


def _looks_like_high_score_promotion_question(message: str) -> bool:
    """Return whether the user asks how score-ranked picks promoted to second board."""

    high_score_terms = ("高分票", "高评分", "评分前", "top10", "Top10", "选出的")
    promotion_terms = ("1进2", "一进二", "晋级二板", "二板成功率", "进二板")
    return any(term in message for term in high_score_terms) and any(
        term in message for term in promotion_terms
    )


def _extract_high_score_review_days(message: str) -> int:
    """Return the requested number of mature Top10 review dates."""

    match = re.search(r"(?:最近|近|过去)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    return max(1, min(int(match.group(1)), 20)) if match else 5


def _requires_high_score_promotion_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether a high-score promotion comparison must be preserved."""

    payload = facts.get("review_high_score_picks")
    return (
        _looks_like_high_score_promotion_question(message)
        and isinstance(payload, dict)
        and int(payload.get("promotion_ready_date_count") or 0) > 0
    )


def _contains_high_score_promotion_facts(
    answer: str,
    facts: dict[str, Any],
) -> bool:
    """Check that the answer reports score-ranked and market promotion samples."""

    payload = facts.get("review_high_score_picks")
    if not isinstance(payload, dict):
        return False
    required_tokens = (
        f"{payload.get('top_pick_promoted_count')}/{payload.get('top_pick_promotion_sample_size')}",
        f"{payload.get('market_promoted_count')}/{payload.get('market_promotion_sample_size')}",
    )
    return all(token in answer for token in required_tokens)


def _limit_up_items_from_facts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Return limit-up rows from either local events or Tonghuashun pool facts."""

    local_payload = facts.get("limit_up_events")
    if isinstance(local_payload, dict) and isinstance(local_payload.get("events"), list):
        return [item for item in local_payload["events"] if isinstance(item, dict)]
    remote_payload = facts.get("remote_limit_up_pool")
    if isinstance(remote_payload, dict) and isinstance(remote_payload.get("items"), list):
        return [item for item in remote_payload["items"] if isinstance(item, dict)]
    return []


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response."""

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM planner did not return a JSON object")
    return parsed


def _normalize_tool_calls(raw_calls: object) -> list[dict[str, Any]]:
    """Normalize planner tool calls into a predictable list."""

    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        arguments = raw.get("arguments") or {}
        if name and isinstance(arguments, dict):
            calls.append({"name": name, "arguments": arguments})
    return calls[:6]


def _normalize_first_board_position_tool_calls(
    request: AgentChatRequest,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Route aggregate K-line position questions to the ratings fact source."""

    if not _looks_like_first_board_position_question(request.message):
        return tool_calls
    normalized = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    if any(call.get("name") == "first_board_ratings" for call in normalized):
        return normalized
    trade_date = request.trade_date or _extract_trade_date(request.message)
    normalized.insert(
        0,
        {
            "name": "first_board_ratings",
            "arguments": {
                "trade_date": trade_date.isoformat() if trade_date else None,
            },
        },
    )
    return normalized[:6]


def _normalize_daily_board_promotion_tool_calls(
    request: AgentChatRequest,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Route daily promotion questions to the adjacent-close cohort tool."""

    if not _looks_like_daily_board_promotion_question(request.message):
        return tool_calls
    normalized = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    if any(call.get("name") == "daily_board_promotion" for call in normalized):
        return normalized
    end_date = request.trade_date or _extract_trade_date(request.message)
    normalized.insert(
        0,
        {
            "name": "daily_board_promotion",
            "arguments": {
                "days": _extract_promotion_days(request.message),
                "end_date": end_date.isoformat() if end_date else None,
            },
        },
    )
    return normalized[:6]


def _execute_llm_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools: AgentToolRegistry,
    *,
    request: AgentChatRequest,
    context_symbol: str | None = None,
) -> ToolExecution:
    """Execute planner-selected tools and return compact facts and traces."""

    facts: dict[str, Any] = {}
    traces: list[AgentToolTrace] = []
    call_names: list[str] = []
    references: list[str] = []
    latest_ratings: FirstBoardRatingsResponse | None = None
    latest_ratings_tool: ToolResult | None = None

    for call in tool_calls:
        name = call["name"]
        arguments = call["arguments"]
        if not tools.is_enabled(name):
            error = (
                f"{name} is unavailable in Agent profile {tools.profile}; "
                "the capability is deferred beyond V1."
            )
            facts[f"{name}_error"] = error
            traces.append(
                _tool_error_trace(
                    name=name,
                    tool_input=arguments,
                    summary="当前 V1 不提供该实时能力。",
                    error=error,
                )
            )
            call_names.append(name)
            continue
        if name == "market_summary":
            include_limit_down = bool(arguments.get("include_limit_down")) or (
                _QuestionSignals.from_message(request.message).market_environment
            )
            result = (
                tools.market_summary(include_limit_down=True)
                if include_limit_down
                else tools.market_summary()
            )
            summary: MarketSummary = result.output
            facts["market_summary"] = result.trace_output
            traces.append(result.trace())
            call_names.append(name)
            references.append(f"trade_date={summary.trade_date.isoformat()}")
            if summary.limit_down_source:
                references.append(f"limit_down_source={summary.limit_down_source}")
        elif name == "market_index_trend":
            days = _parse_optional_int(arguments.get("days")) or (
                _extract_market_index_days(request.message)
            )
            end_date = _explicit_request_trade_date(request)
            try:
                result = tools.market_index_trend(
                    days=max(2, min(days, 20)),
                    end_date=end_date,
                )
            except Exception as error:  # noqa: BLE001
                facts["market_index_trend_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={
                            "days": days,
                            "end_date": end_date.isoformat() if end_date else None,
                        },
                        summary="大盘指数区间走势查询失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response: MarketIndexTrendFacts = result.output
            facts["market_index_trend"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"index_data_as_of={response.data_as_of.isoformat()}",
                    *[
                        f"index_source={item.source}"
                        for item in response.indices
                    ],
                ]
            )
        elif name == "daily_board_promotion":
            days = _parse_optional_int(arguments.get("days")) or 5
            end_date = _explicit_request_trade_date(request)
            result = tools.daily_board_promotion(
                days=max(1, min(days, 60)),
                end_date=end_date,
            )
            facts["daily_board_promotion"] = result.trace_output
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                f"promotion_trade_date={item.trade_date.isoformat()}"
                for item in result.output
            )
        elif name == "sector_performance":
            sector = _optional_str(arguments.get("sector"))
            if sector is None:
                sector = _extract_sector_query(request.message)
            trade_date = _explicit_request_trade_date(request)
            try:
                result = tools.sector_performance(
                    sector=sector,
                    trade_date=trade_date,
                )
            except Exception as error:  # noqa: BLE001
                facts["sector_performance_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={
                            "sector": sector,
                            "trade_date": trade_date.isoformat() if trade_date else None,
                        },
                        summary="板块行情查询失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["sector_performance"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"sector={response.sector_name or 'industry-ranking'}",
                    f"data_as_of={response.data_as_of.isoformat()}",
                    *[f"source={source}" for source in response.sources],
                ]
            )
        elif name == "hot_stock_ranking":
            period = _optional_str(arguments.get("period")) or "day"
            limit = (
                extract_result_limit(request.message)
                or _parse_optional_int(arguments.get("limit"))
                or 20
            )
            requested_source = _optional_str(arguments.get("source")) or "auto"
            enrich_performance = bool(arguments.get("enrich_performance")) or (
                _QuestionSignals.from_message(request.message).market_environment
            )
            if "同花顺" in request.message:
                requested_source = "tonghuashun"
            elif "东方财富" in request.message:
                requested_source = "eastmoney"
            try:
                hot_stock_arguments: dict[str, Any] = {
                    "period": period,
                    "limit": max(1, min(limit, 100)),
                    "source": requested_source,
                }
                if enrich_performance:
                    hot_stock_arguments["enrich_performance"] = True
                result = tools.hot_stock_ranking(**hot_stock_arguments)
            except Exception as error:  # noqa: BLE001
                facts["hot_stock_ranking_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                    tool_input={
                        "period": period,
                        "limit": limit,
                        "source": requested_source,
                    },
                        summary="同花顺热股榜查询失败，已将原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            facts["hot_stock_ranking"] = result.output
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"source={result.output.get('source')}",
                    f"captured_at={result.output.get('captured_at')}",
                    f"data_fresh={result.output.get('data_fresh')}",
                ]
            )
        elif name == "dragon_tiger_list":
            trade_date = _latest_external_trade_date(request, tools.events)
            board_type = _optional_str(arguments.get("board_type")) or "all"
            query = _optional_str(arguments.get("query"))
            limit = _parse_optional_int(arguments.get("limit")) or 30
            try:
                result = tools.dragon_tiger_list(
                    trade_date=trade_date,
                    board_type=board_type,
                    query=query,
                    limit=max(1, min(limit, 100)),
                )
            except Exception as error:  # noqa: BLE001
                facts["dragon_tiger_list_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={
                            "trade_date": trade_date.isoformat() if trade_date else None,
                            "board_type": board_type,
                            "query": query,
                            "limit": limit,
                        },
                        summary="同花顺龙虎榜查询失败，已将原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            facts["dragon_tiger_list"] = result.output
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    "source=hithink-finance",
                    f"trade_date={result.output.get('trade_date')}",
                ]
            )
        elif name == "remote_limit_up_pool":
            trade_date = _explicit_request_trade_date(request)
            board_height = (
                _parse_optional_int(arguments.get("board_height"))
                or _extract_board_height(request.message)
            )
            limit = _parse_optional_int(arguments.get("limit")) or (
                100 if _looks_like_exhaustive_list_request(request.message) else 30
            )
            exclude_st = _parse_optional_bool(arguments.get("exclude_st"))
            exclude_new = _parse_optional_bool(arguments.get("exclude_new"))
            try:
                result = tools.remote_limit_up_pool(
                    trade_date=trade_date,
                    board_height=board_height,
                    query=_optional_str(arguments.get("query")),
                    exclude_st=True if exclude_st is None else exclude_st,
                    exclude_new=True if exclude_new is None else exclude_new,
                    limit=max(1, min(limit, 100)),
                )
            except Exception as error:  # noqa: BLE001
                facts["remote_limit_up_pool_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="同花顺涨停池查询失败，已将原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            facts["remote_limit_up_pool"] = result.output
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    "source=hithink-finance",
                    f"trade_date={result.output.get('trade_date')}",
                ]
            )
        elif name == "finance_news":
            query = _optional_str(arguments.get("query"))
            limit = _parse_optional_int(arguments.get("limit")) or 8
            hours = _parse_optional_int(arguments.get("hours")) or 48
            try:
                result = tools.finance_news(
                    query=query,
                    limit=max(1, min(limit, 12)),
                    hours=max(1, min(hours, 168)),
                )
            except Exception as error:  # noqa: BLE001
                facts["finance_news_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={"query": query, "limit": limit, "hours": hours},
                        summary="财经快讯聚合失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["finance_news"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(item.url for item in response.items)
        elif name == "stock_news":
            days = _parse_optional_int(arguments.get("days")) or (
                _extract_stock_news_days(request.message)
            )
            limit = _parse_optional_int(arguments.get("limit")) or 10
            try:
                target = _resolve_tool_stock_target(
                    tools=tools,
                    request=request,
                    argument_value=_optional_str(arguments.get("symbol")),
                    context_symbol=context_symbol,
                )
                result = tools.stock_news(
                    target,
                    days=max(1, min(days, 30)),
                    limit=max(1, min(limit, 20)),
                )
            except Exception as error:  # noqa: BLE001
                facts["stock_news_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={"symbol": arguments.get("symbol"), "days": days, "limit": limit},
                        summary="个股资讯查询失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["stock_news"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(item.url for item in response.items)
        elif name == "stock_activity":
            days = _parse_optional_int(arguments.get("days")) or (
                _extract_stock_news_days(request.message)
            )
            news_limit = _parse_optional_int(arguments.get("news_limit")) or 8
            try:
                target = _resolve_tool_stock_target(
                    tools=tools,
                    request=request,
                    argument_value=_optional_str(arguments.get("symbol")),
                    context_symbol=context_symbol,
                )
                result = tools.stock_activity(
                    target,
                    days=max(1, min(days, 30)),
                    news_limit=max(1, min(news_limit, 20)),
                )
            except Exception as error:  # noqa: BLE001
                facts["stock_activity_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={
                            "symbol": arguments.get("symbol"),
                            "days": days,
                            "news_limit": news_limit,
                        },
                        summary="个股近期动态查询失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["stock_activity"] = result.trace_output
            traces.append(result.trace())
            call_names.append(name)
            references.extend(item.url for item in response.news.items)
        elif name == "web_search":
            query = _optional_str(arguments.get("query")) or request.message
            limit = _parse_optional_int(arguments.get("limit")) or 5
            try:
                result = tools.web_search(
                    query=query,
                    limit=max(1, min(limit, 8)),
                )
            except Exception as error:  # noqa: BLE001
                facts["web_search_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={"query": query, "limit": limit},
                        summary="通用搜索失败，已将失败原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["web_search"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(item.url for item in response.results)
        elif name == "limit_up_events":
            arguments = _normalize_limit_up_event_arguments(
                request,
                arguments,
            )
            trade_date = _parse_optional_date(arguments.get("trade_date"))
            if trade_date and not _has_events_for_date(tools.events, trade_date):
                available_dates = sorted(
                    {event.trade_date for event in tools.events},
                    reverse=True,
                )
                facts["limit_up_events_error"] = {
                    "requested_trade_date": trade_date.isoformat(),
                    "reason": "No local limit-up events for requested date.",
                    "latest_local_trade_date": (
                        available_dates[0].isoformat() if available_dates else None
                    ),
                    "available_trade_dates": [
                        item.isoformat() for item in available_dates[:20]
                    ],
                }
                call_names.append(name)
                references.append(f"missing_trade_date={trade_date.isoformat()}")
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary=f"{trade_date.isoformat()} 本地暂无涨停事件数据。",
                        error="No local limit-up events for requested date.",
                    )
                )
                continue
            contract = build_limit_up_query_contract(
                request.message,
                request_trade_date=request.trade_date,
                planner_arguments=arguments,
            )
            result = tools.limit_up_events(
                trade_date=contract.trade_date,
                board_height=contract.board_height,
                min_board_height=contract.min_board_height,
                highest_only=contract.highest_only,
                market=contract.market,
                query=contract.query,
                event_status=contract.event_status,
                sort_by=contract.sort_by,
                sort_order=contract.sort_order,
                limit=contract.limit,
            )
            result.input["query_contract"] = contract.to_dict()
            result.trace_output["query_contract"] = contract.to_dict()
            facts["limit_up_events"] = {
                "trade_date": result.trace_output.get("trade_date"),
                "market": result.trace_output.get("market"),
                "market_label": result.trace_output.get("market_label"),
                "matched_count": result.trace_output.get("matched_count"),
                "returned_count": result.trace_output.get("returned_count"),
                "query_contract": contract.to_dict(),
                "events": [_event_fact(event) for event in result.output],
            }
            traces.append(result.trace())
            call_names.append(name)
            references.append(f"trade_date={result.trace_output.get('trade_date')}")
        elif name == "stock_kline":
            raw_symbol = str(
                arguments.get("symbol") or arguments.get("query") or ""
            ).strip()
            days = _parse_optional_int(arguments.get("days")) or 20
            end_date = _explicit_request_trade_date(request)
            if not raw_symbol:
                facts["stock_kline_error"] = "symbol is required"
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="K线工具缺少股票代码或名称，未执行查询。",
                        error="symbol is required",
                    )
                )
                continue
            try:
                result = tools.stock_kline(
                    symbol=raw_symbol,
                    days=max(5, min(days, 60)),
                    end_date=end_date,
                )
            except Exception as error:  # noqa: BLE001
                facts["stock_kline_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="K线工具查询失败，已将原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response = result.output
            facts["stock_kline"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"symbol={response.symbol}",
                    f"data_as_of={response.data_as_of.isoformat()}",
                ]
            )
        elif name == "first_board_ratings":
            trade_date = _explicit_request_trade_date(request)
            if trade_date and not _has_events_for_date(tools.events, trade_date):
                available_dates = sorted(
                    {event.trade_date for event in tools.events},
                    reverse=True,
                )
                facts["first_board_ratings_error"] = {
                    "requested_trade_date": trade_date.isoformat(),
                    "reason": "No local first-board events for requested date.",
                    "latest_local_trade_date": (
                        available_dates[0].isoformat() if available_dates else None
                    ),
                    "available_trade_dates": [
                        item.isoformat() for item in available_dates[:20]
                    ],
                }
                call_names.append(name)
                references.append(f"missing_trade_date={trade_date.isoformat()}")
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input={"trade_date": trade_date.isoformat()},
                        summary=(
                            f"{trade_date.isoformat()} 本地暂无首板涨停数据，"
                            "已将缺失原因交给 LLM 回答。"
                        ),
                        error="No local first-board events for requested date.",
                    )
                )
                continue
            result = tools.first_board_ratings(trade_date=trade_date)
            latest_ratings_tool = result
            latest_ratings = result.output
            facts["first_board_ratings"] = _compact_ratings_facts(latest_ratings)
            if result.trace_output.get("auction_final"):
                facts["first_board_ratings"]["auction_final"] = (
                    result.trace_output["auction_final"]
                )
            traces.append(result.trace())
            call_names.append(name)
            references.append(f"trade_date={latest_ratings.trade_date.isoformat()}")
        elif name == "first_board_discovery":
            raw_data_as_of = arguments.get("data_as_of")
            try:
                data_as_of = (
                    date.fromisoformat(str(raw_data_as_of))
                    if raw_data_as_of
                    else None
                )
                result = tools.first_board_discovery(data_as_of=data_as_of)
            except Exception as error:  # noqa: BLE001
                facts["first_board_discovery_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="首板挖掘快照不可用，已将缺失原因交给 LLM。",
                        error=str(error),
                    )
                )
                call_names.append(name)
                continue
            response: FirstBoardDiscoveryResponse = result.output
            facts["first_board_discovery"] = result.trace_output
            traces.append(result.trace())
            call_names.append(name)
            references.append(f"data_as_of={response.data_as_of.isoformat()}")
        elif name == "first_board_filter":
            if latest_ratings is None:
                result = tools.first_board_ratings(trade_date=None)
                latest_ratings_tool = result
                latest_ratings = result.output
                facts["first_board_ratings"] = _compact_ratings_facts(latest_ratings)
                traces.append(result.trace())
                call_names.append("first_board_ratings")
                references.append(f"trade_date={latest_ratings.trade_date.isoformat()}")
            query = str(arguments.get("query") or arguments.get("filter") or "").strip()
            filter_query = _filter_query_from_context(query or "\u7528\u6237\u95ee\u53e5")
            matches = _filter_first_board_candidates(latest_ratings, filter_query)
            facts["first_board_filter"] = {
                "query": filter_query.label,
                "matched_count": len(matches),
                "matches": [_rating_fact(item) for item in matches[:12]],
            }
            traces.append(
                _build_first_board_filter_trace(latest_ratings, filter_query, matches)
            )
            call_names.append(name)
            references.append(f"filter={filter_query.label}")
        elif name == "prediction_quality_audit":
            available_dates = sorted({event.trade_date for event in tools.events})
            if not available_dates:
                facts["prediction_quality_audit_error"] = (
                    "No local limit-up events available."
                )
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="本地没有涨停数据，无法执行预测质量审计。",
                        error="No local limit-up events available.",
                    )
                )
                continue
            start_date = (
                _parse_optional_date(arguments.get("start_date"))
                or available_dates[0]
            )
            end_date = (
                _parse_optional_date(arguments.get("end_date"))
                or available_dates[-1]
            )
            top_k = _parse_optional_int(arguments.get("top_k")) or 10
            result = tools.prediction_quality_audit(
                start_date=start_date,
                end_date=end_date,
                scoring_version=_optional_str(arguments.get("scoring_version")),
                top_k=max(3, min(top_k, 30)),
            )
            response = result.output
            facts["prediction_quality_audit"] = compact_prediction_quality_audit(
                response
            )
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"start_date={response.start_date.isoformat()}",
                    f"end_date={response.end_date.isoformat()}",
                    f"scoring_version={response.audited_scoring_version}",
                ]
            )
        elif name == "rating_backtest":
            available_dates = sorted({event.trade_date for event in tools.events})
            if not available_dates:
                facts["rating_backtest_error"] = "No local limit-up events available."
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="本地没有涨停数据，无法执行评分回测。",
                        error="No local limit-up events available.",
                    )
                )
                continue
            end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
            start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
                max(0, len(available_dates) - 20)
            ]
            failure_limit = int(arguments.get("failure_limit") or 8)
            result = tools.rating_backtest(
                start_date=start_date,
                end_date=end_date,
                failure_limit=max(0, min(failure_limit, 30)),
            )
            response = result.output
            facts["rating_backtest"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"start_date={response.start_date.isoformat()}",
                    f"end_date={response.end_date.isoformat()}",
                ]
            )
        elif name == "first_board_critic":
            symbol = str(arguments.get("symbol") or "").strip()
            trade_date = _explicit_request_trade_date(request)
            if not symbol:
                facts["first_board_critic_error"] = "symbol is required"
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="Critic tool skipped because symbol is missing.",
                        error="symbol is required",
                    )
                )
                continue
            try:
                result = tools.first_board_critic(
                    symbol=symbol,
                    trade_date=trade_date,
                )
            except ValueError as error:
                facts["first_board_critic_error"] = str(error)
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="Critic review failed; the failure reason is passed to the LLM.",
                        error=str(error),
                    )
                )
                continue
            response = result.output
            facts["first_board_critic"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"symbol={response.symbol}",
                    f"trade_date={response.trade_date.isoformat()}",
                    f"critic_verdict={response.verdict}",
                ]
            )
        elif name == "rating_evaluation":
            available_dates = sorted({event.trade_date for event in tools.events})
            if not available_dates:
                facts["rating_evaluation_error"] = "No local limit-up events available."
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="No local limit-up events; evaluation cannot run.",
                        error="No local limit-up events available.",
                    )
                )
                continue
            end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
            start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
                max(0, len(available_dates) - 20)
            ]
            limit = int(arguments.get("limit") or 30)
            result = tools.rating_evaluation(
                start_date=start_date,
                end_date=end_date,
                limit=max(1, min(limit, 100)),
            )
            response = result.output
            facts["rating_evaluation"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"start_date={response.start_date.isoformat()}",
                    f"end_date={response.end_date.isoformat()}",
                ]
            )
        elif name == "review_high_score_picks":
            available_dates = sorted({event.trade_date for event in tools.events})
            if not available_dates:
                facts["review_high_score_picks_error"] = "No local limit-up events available."
                traces.append(
                    _tool_error_trace(
                        name=name,
                        tool_input=arguments,
                        summary="No local limit-up events; Review Agent cannot run.",
                        error="No local limit-up events available.",
                    )
                )
                continue
            end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
            start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
                max(0, len(available_dates) - 6)
            ]
            min_score_value = arguments.get("min_score")
            min_score = float(min_score_value) if min_score_value is not None else 0
            if _looks_like_high_score_promotion_question(request.message):
                min_score = 0
                review_days = _extract_high_score_review_days(request.message)
                end_date = available_dates[-1]
                start_date = available_dates[
                    max(0, len(available_dates) - review_days - 1)
                ]
            top_per_day = _parse_optional_int(arguments.get("top_per_day")) or 10
            result = tools.review_high_score_picks(
                start_date=start_date,
                end_date=end_date,
                min_score=max(0, min(min_score, 100)),
                top_per_day=max(1, min(top_per_day, 20)),
            )
            response = result.output
            facts["review_high_score_picks"] = response.model_dump(mode="json")
            traces.append(result.trace())
            call_names.append(name)
            references.extend(
                [
                    f"start_date={response.start_date.isoformat()}",
                    f"end_date={response.end_date.isoformat()}",
                    f"review_sample_size={response.sample_size}",
                ]
            )
        elif name == "scoring_policy_status":
            result = tools.scoring_policy_status()
            payload = result.output
            facts["scoring_policy_status"] = payload
            traces.append(result.trace())
            call_names.append(name)
            champion = payload.get("champion") or {}
            latest = payload.get("latest_optimization") or {}
            challenger = latest.get("challenger_policy") or {}
            references.extend(
                [
                    f"scoring_version={champion.get('version')}",
                    f"challenger_version={challenger.get('version')}",
                ]
            )
        else:
            facts[f"{name}_error"] = "Unsupported tool requested by LLM planner."
            traces.append(
                _tool_error_trace(
                    name=name,
                    tool_input=arguments,
                    summary=f"LLM 请求了未注册工具 {name}，后端已跳过。",
                    error="Unsupported tool requested by LLM planner.",
                )
            )

    return {
        "facts": facts,
        "tool_results": traces,
        "tool_call_names": call_names,
        "references": list(dict.fromkeys(references)),
    }


def _resolve_tool_stock_target(
    *,
    tools: AgentToolRegistry,
    request: AgentChatRequest,
    argument_value: str | None,
    context_symbol: str | None,
) -> str:
    """Resolve planner, request and conversation stock hints in priority order."""

    for candidate in (
        argument_value,
        request.symbol,
        _extract_symbol_hint(request.message),
        request.message,
        context_symbol,
    ):
        if not candidate:
            continue
        try:
            return tools.resolve_stock_identity(candidate)[0]
        except ValueError:
            continue
    raise ValueError("Cannot resolve the requested stock.")


def _normalize_limit_up_event_arguments(
    request: AgentChatRequest,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Apply Query Contract v2 to planner-proposed event arguments."""

    if not _looks_like_limit_up_event_question(request.message):
        return arguments
    contract = build_limit_up_query_contract(
        request.message,
        request_trade_date=request.trade_date,
        planner_arguments=arguments,
    )
    return contract.to_tool_arguments()


def _deterministic_pre_llm_response(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
) -> AgentChatResponse | None:
    """Handle stable product and availability questions before LLM planning."""

    plan = _build_agent_plan(request=request, context=_SessionContext())
    if plan.intent == "capability_intro":
        return _with_plan_trace(
            _answer_static_text(request, "capability_intro", TEXT["capability"]),
            plan,
        )
    if plan.intent == "market_schedule":
        latest_date = max((event.trade_date for event in events), default=date.today())
        return _with_plan_trace(_answer_market_schedule(request, latest_date), plan)
    if (
        plan.trade_date
        and _QuestionSignals.from_message(request.message).needs_local_event_date
        and not _has_events_for_date(events, plan.trade_date)
    ):
        return _with_plan_trace(
            _answer_missing_trade_date(request, plan.trade_date, events),
            plan,
        )
    return None


def _answer_stock_kline_without_llm(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    context: "_SessionContext",
) -> AgentChatResponse | None:
    """Use the K-line tool as a deterministic fallback when the LLM is unavailable."""

    if not _looks_like_stock_kline_question(request.message):
        return None
    target = AgentToolPolicyEngine(tools).resolve_stock_target(
        request,
        context_symbol=context.symbol,
    )
    if target is None:
        return None
    try:
        result = tools.stock_kline(
            symbol=target,
            days=_extract_kline_days(request.message),
            end_date=request.trade_date,
        )
    except Exception:
        return None
    response = result.output
    facts = {"stock_kline": response.model_dump(mode="json")}
    return AgentChatResponse(
        session_id=request.session_id,
        intent="stock_trend",
        answer=_template_answer_from_tool_facts(
            request=request,
            intent="stock_trend",
            facts=facts,
        ),
        tool_calls=["stock_kline", "template_general_answer"],
        tool_results=[result.trace()],
        references=[
            f"symbol={response.symbol}",
            f"data_as_of={response.data_as_of.isoformat()}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_market_index_trend_without_llm(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
) -> AgentChatResponse | None:
    """Answer broad-index trend questions when the LLM is unavailable."""

    if not _QuestionSignals.from_message(request.message).market_index_trend:
        return None
    try:
        result = tools.market_index_trend(
            days=_extract_market_index_days(request.message),
            end_date=request.trade_date or _extract_trade_date(request.message),
        )
    except Exception:
        return None
    response: MarketIndexTrendFacts = result.output
    facts = {"market_index_trend": response.model_dump(mode="json")}
    return AgentChatResponse(
        session_id=request.session_id,
        intent="market_index_trend",
        answer=_template_answer_from_tool_facts(
            request=request,
            intent="market_index_trend",
            facts=facts,
        ),
        tool_calls=["market_index_trend", "template_general_answer"],
        tool_results=[result.trace()],
        references=[
            f"index_data_as_of={response.data_as_of.isoformat()}",
            *[f"index_source={item.source}" for item in response.indices],
        ],
        warnings=[
            _safety_warning(),
            "LLM unavailable; deterministic major-index trend summary used.",
        ],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_daily_board_promotion_without_llm(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
) -> AgentChatResponse | None:
    """Answer promotion-rate questions deterministically when the LLM is unavailable."""

    if not _looks_like_daily_board_promotion_question(request.message):
        return None
    result = tools.daily_board_promotion(
        days=_extract_promotion_days(request.message),
        end_date=request.trade_date or _extract_trade_date(request.message),
    )
    facts = result.trace_output
    return AgentChatResponse(
        session_id=request.session_id,
        intent="daily_board_promotion",
        answer=_ensure_safety_boundary(
            _template_daily_board_promotion_answer(facts)
        ),
        tool_calls=["daily_board_promotion", "template_general_answer"],
        tool_results=[result.trace()],
        references=[
            f"promotion_trade_date={item.trade_date.isoformat()}"
            for item in result.output
        ],
        warnings=[
            _safety_warning(),
            "LLM unavailable; deterministic daily promotion statistics used.",
        ],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_prediction_quality_without_llm(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
) -> AgentChatResponse | None:
    """Audit prediction quality deterministically when the LLM is unavailable."""

    if not _QuestionSignals.from_message(request.message).prediction_quality:
        return None
    available_dates = sorted({event.trade_date for event in tools.events})
    if not available_dates:
        return None
    try:
        result = tools.prediction_quality_audit(
            start_date=available_dates[0],
            end_date=available_dates[-1],
            top_k=10,
        )
    except Exception:
        return None
    response = result.output
    facts = {
        "prediction_quality_audit": compact_prediction_quality_audit(response)
    }
    return AgentChatResponse(
        session_id=request.session_id,
        intent="prediction_quality_audit",
        answer=_template_answer_from_tool_facts(
            request=request,
            intent="prediction_quality_audit",
            facts=facts,
        ),
        tool_calls=["prediction_quality_audit", "template_general_answer"],
        tool_results=[result.trace()],
        references=[
            f"start_date={response.start_date.isoformat()}",
            f"end_date={response.end_date.isoformat()}",
            f"scoring_version={response.audited_scoring_version}",
        ],
        warnings=[
            _safety_warning(),
            "LLM unavailable; deterministic prediction-quality summary used.",
        ],
        generated_by=CHAT_AGENT_VERSION,
    )


def _template_daily_board_promotion_answer(payload: dict[str, Any]) -> str:
    """Render daily empirical board-promotion rates from tool facts."""

    items = payload.get("items") or []
    if not items:
        return "本地没有足够的相邻交易日收盘数据，暂时无法计算每日连板晋级率。"

    def cohort_text(item: dict[str, Any], prefix: str) -> str:
        sample_size = int(item.get(f"{prefix}_sample_size") or 0)
        promoted_count = int(item.get(f"{prefix}_promoted_count") or 0)
        probability = item.get(f"{prefix}_probability")
        return (
            f"{promoted_count}/{sample_size}（{float(probability):.1%}）"
            if sample_size and probability is not None
            else "无样本"
        )

    lines = [
        "每日连板晋级率按前一交易日收盘封住的股票计算，晋级日口径如下："
    ]
    for item in items:
        lines.append(
            f"- {item.get('trade_date')}：总晋级 "
            f"{item.get('promoted_count')}/{item.get('sample_size')}"
            f"（{float(item.get('probability') or 0):.1%}）；"
            f"首板→二板 {cohort_text(item, 'first_board')}；"
            f"连板梯队继续晋级 {cohort_text(item, 'continued_board')}。"
        )

    latest = items[-1]
    buckets = latest.get("buckets") or []
    if buckets:
        details = IDEOGRAPHIC_COMMA.join(
            f"{bucket.get('from_board_height')}→{bucket.get('to_board_height')}板 "
            f"{bucket.get('promoted_count')}/{bucket.get('sample_size')}"
            f"（{float(bucket.get('probability') or 0):.1%}）"
            for bucket in buckets
        )
        lines.append(f"最新交易日分层：{details}。")
    promoted_stocks = latest.get("promoted_stocks") or []
    if promoted_stocks:
        lines.append(
            f"最新交易日晋级成功 {len(promoted_stocks)} 只："
            + IDEOGRAPHIC_COMMA.join(
                f"{stock.get('name')}({stock.get('symbol')}) "
                f"{stock.get('from_board_height')}→{stock.get('to_board_height')}板"
                for stock in promoted_stocks
            )
            + "。"
        )
    else:
        lines.append("最新交易日没有识别到收盘晋级成功的股票。")
    lines.append("该指标是已发生样本的经验比例，用于描述接力环境，不代表未来成功概率。")
    return "\n".join(lines)


def _template_first_board_position_answer(ratings: dict[str, Any]) -> str:
    """Render a complete K-line position grouping when the final LLM is unavailable."""

    classification = ratings.get("position_classification") or {}
    groups = classification.get("groups") or []
    candidate_count = int(
        classification.get("candidate_count") or ratings.get("candidate_count") or 0
    )
    classified_count = int(classification.get("classified_count") or 0)
    missing = classification.get("missing_candidates") or []
    lines = [
        (
            f"{ratings.get('trade_date')} 首板评级候选池共 {candidate_count} 只，"
            "按首板前 K 线位置分类如下（这里的位置不是首封时间）："
        ),
        (
            f"已分类 {classified_count} 只，位置数据缺失 {len(missing)} 只。"
            f"{classification.get('scope_note') or ''}"
        ),
    ]
    for group in groups:
        candidates = group.get("candidates") or []
        names = IDEOGRAPHIC_COMMA.join(
            f"{item.get('name')}({item.get('symbol')}) "
            f"{item.get('rating')}/{float(item.get('score') or 0):.1f}"
            for item in candidates
        )
        lines.append(
            f"- {group.get('label')}（{group.get('count')}只，"
            f"平均分 {float(group.get('avg_score') or 0):.1f}）：{names or '暂无'}"
        )
    if missing:
        names = IDEOGRAPHIC_COMMA.join(
            f"{item.get('name')}({item.get('symbol')})" for item in missing
        )
        lines.append(f"- 位置数据缺失（{len(missing)}只）：{names}")
    filtered_out_count = int(ratings.get("filtered_out_count") or 0)
    if filtered_out_count:
        lines.append(
            f"另有 {filtered_out_count} 只未通过评级候选过滤，未纳入本次位置分类。"
        )
    return "\n".join(lines)


def _tool_error_trace(
    name: str,
    tool_input: dict[str, Any],
    summary: str,
    error: str,
) -> AgentToolTrace:
    """Build a trace for skipped or failed planner tool calls."""

    return AgentToolTrace(
        name=name,
        input=tool_input,
        summary=summary,
        status="error",
        output={},
        error=error,
    )


def _compact_ratings_facts(ratings: FirstBoardRatingsResponse) -> dict[str, Any]:
    """Serialize rating facts into a compact shape for the final LLM answer."""

    return {
        "trade_date": ratings.trade_date.isoformat(),
        "candidate_count": len(ratings.candidates),
        "filtered_out_count": len(ratings.filtered_out),
        "top_candidates": [
            _rating_fact(item) if index < 5 else _brief_rating_fact(item)
            for index, item in enumerate(ratings.candidates[:10])
        ],
        "industry_distribution": _summarize_first_board_industries(ratings.candidates)[:12],
        "position_classification": compact_first_board_position_groups(
            ratings.candidates
        ),
    }


def _llm_plan_trace(
    tool_plan: dict[str, Any],
    model: str,
    provider: str,
    duration_ms: int | None = None,
    prompt_chars: int = 0,
    completion_chars: int = 0,
) -> AgentToolTrace:
    """Expose the LLM planner decision as an Agent trace."""

    return AgentToolTrace(
        name="llm_tool_planner",
        input={
            "model": model,
            "provider": provider,
            "intent_label": tool_plan.get("intent_label"),
            "skill_name": tool_plan.get("skill_name"),
            "capabilities": tool_plan.get("capabilities") or [],
            "context_mode": tool_plan.get("context_mode") or "standalone",
            "context_capabilities": tool_plan.get("context_capabilities") or [],
            "safety": tool_plan.get("safety"),
            "tool_calls": tool_plan.get("tool_calls") or [],
        },
        summary="\u7531 LLM \u6839\u636e\u5de5\u5177\u63cf\u8ff0\u751f\u6210\u5de5\u5177\u8c03\u7528\u8ba1\u5212\u3002",
        output={
            "prompt_chars": prompt_chars,
            "completion_chars": completion_chars,
        },
        duration_ms=duration_ms,
    )


class _SessionContext:
    """Minimal chat context recovered from recent Agent runs."""

    def __init__(
        self,
        symbol: str | None = None,
        trade_date: date | None = None,
        filter_query: _FirstBoardFilterQuery | None = None,
        matched_symbols: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        last_capabilities: list[str] | None = None,
    ):
        self.symbol = symbol
        self.trade_date = trade_date
        self.filter_query = filter_query
        self.matched_symbols = matched_symbols or []
        self.conversation_history = conversation_history or []
        self.last_capabilities = last_capabilities or []


def _build_agent_plan(
    request: AgentChatRequest,
    context: _SessionContext,
) -> _AgentPlan:
    """Plan intent and tool steps from the user's natural-language question."""

    parsed_trade_date = _extract_trade_date(request.message)
    trade_date = parsed_trade_date or request.trade_date
    if trade_date is None and _looks_like_context_date_question(request.message):
        trade_date = context.trade_date
    filter_query = _extract_first_board_filter(request.message)
    if filter_query is None and _looks_like_context_pool_question(request.message):
        filter_query = context.filter_query
    intent = _detect_intent(request.message, request.intent_hint)
    if _looks_like_general_limit_up_question(request.message):
        intent = "limit_up_query"
    if (
        filter_query
        and context.matched_symbols
        and _looks_like_context_top_question(request.message)
    ):
        intent = "first_board_context_top"
    if _looks_like_first_board_sector_question(request.message):
        intent = "first_board_sector_summary"
    if (
        parsed_trade_date
        and _looks_like_first_board_data_question(request.message)
        and _mentions_first_board_scope(request.message)
        and filter_query is None
        and intent != "first_board_sector_summary"
        and not _looks_like_general_limit_up_question(request.message)
    ):
        intent = "today_summary"
    if (
        filter_query
        and _looks_like_first_board_data_question(request.message)
        and _mentions_first_board_scope(request.message)
        and not _looks_like_general_limit_up_question(request.message)
    ):
        intent = "first_board_filter"
    symbol = request.symbol or _extract_symbol_hint(request.message)
    if symbol is None and _looks_like_context_symbol_question(request.message):
        symbol = context.symbol
    if intent == "rating_explain" and symbol is None and _looks_like_top_candidate_question(request.message):
        intent = "today_summary"
    tool_steps = _plan_tool_steps(
        intent=intent,
        trade_date=trade_date,
        symbol=symbol,
        message=request.message,
    )
    if filter_query and not any(step["name"] == "first_board_filter" for step in tool_steps):
        tool_steps.append(
            {
                "name": "first_board_filter",
                "input": {
                    "filter": filter_query.label,
                    "fields": ["name", "industry", "concept"],
                },
            }
        )

    rationale = _plan_rationale(
        intent=intent,
        trade_date=trade_date,
        filter_query=filter_query,
        symbol=symbol,
        tool_steps=tool_steps,
    )
    return _AgentPlan(
        intent=intent,
        trade_date=trade_date,
        parsed_trade_date=parsed_trade_date,
        filter_query=filter_query,
        symbol=symbol,
        tool_steps=tool_steps,
        rationale=rationale,
    )


def _plan_tool_steps(
    intent: str,
    trade_date: date | None,
    symbol: str | None,
    message: str,
) -> list[dict]:
    """Return the deterministic tools needed for an intent."""

    dated_input = {"trade_date": trade_date.isoformat() if trade_date else None}
    if intent == "greeting":
        return []
    if intent in {"capability_intro", "smalltalk", "out_of_scope", "unsafe_investment_advice"}:
        return []
    if intent == "market_schedule":
        return [{"name": "market_summary", "input": {}}]
    if intent == "market_context":
        return [{"name": "market_summary", "input": {}}]
    if intent == "limit_up_query":
        contract = build_limit_up_query_contract(
            message,
            request_trade_date=trade_date,
        )
        return [
            {
                "name": "limit_up_events",
                "input": contract.to_tool_arguments(),
            }
        ]
    if intent == "first_board_filter":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {
                "name": "first_board_filter",
                "input": {"fields": ["name", "industry", "concept"]},
            },
        ]
    if intent == "first_board_context_top":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {
                "name": "first_board_filter",
                "input": {"fields": ["name", "industry", "concept"]},
            },
        ]
    if intent == "first_board_sector_summary":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {"name": "llm_answer", "input": {"mode": "tool_facts_only"}},
        ]
    if intent in {"rating_explain", "risk_summary", "llm_explanation", "today_summary"}:
        return [{"name": "first_board_ratings", "input": dated_input}]
    return [
        {"name": "market_summary", "input": {}},
        {"name": "first_board_ratings", "input": dated_input},
        {"name": "llm_answer", "input": {"mode": "tool_facts_only"}},
    ]


def _plan_rationale(
    intent: str,
    trade_date: date | None,
    filter_query: _FirstBoardFilterQuery | None,
    symbol: str | None,
    tool_steps: list[dict],
) -> str:
    """Build a short human-readable summary of the plan."""

    parts = [f"\u7406\u89e3\u4e3a {intent}"]
    if trade_date:
        parts.append(f"\u4ea4\u6613\u65e5 {trade_date.isoformat()}")
    if filter_query:
        parts.append(f"\u7b5b\u9009 {filter_query.label}")
    if symbol:
        parts.append(f"\u80a1\u7968 {symbol}")
    tool_names = IDEOGRAPHIC_COMMA.join(step["name"] for step in tool_steps) or "\u65e0\u5de5\u5177"
    parts.append(f"\u8ba1\u5212\u5de5\u5177\uff1a{tool_names}")
    return SEMI.join(parts)


def _extract_symbol_hint(message: str) -> str | None:
    """Extract a six-digit A-share symbol from free-form text."""

    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


def _with_plan_trace(
    response: AgentChatResponse,
    plan: _AgentPlan,
) -> AgentChatResponse:
    """Attach the plan trace to every response without changing tool calls."""

    if plan.symbol is None:
        for reference in response.references:
            if reference.startswith("symbol="):
                plan.symbol = reference.split("=", 1)[1]
                break
    response.tool_results = [plan.trace(), *response.tool_results]
    response.evidence_cards = build_agent_evidence_cards(
        response.tool_results,
        response.warnings,
    )
    response.tool_policy = build_agent_tool_policy_audit(
        tool_calls=response.tool_calls,
        tool_results=response.tool_results,
        warnings=response.warnings,
    )
    return response


def _answer_greeting(request: AgentChatRequest) -> AgentChatResponse:
    """Return a greeting without invoking market analysis tools."""

    return AgentChatResponse(
        session_id=request.session_id,
        intent="greeting",
        answer=TEXT["greeting"],
        tool_calls=[],
        references=[],
        warnings=[],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_static_text(
    request: AgentChatRequest,
    intent: str,
    answer: str,
) -> AgentChatResponse:
    """Return a non-tool conversational answer."""

    return AgentChatResponse(
        session_id=request.session_id,
        intent=intent,
        answer=answer,
        tool_calls=[],
        references=[],
        warnings=[],
        generated_by=CHAT_AGENT_VERSION,
    )


def _has_usable_tool_facts(facts: dict[str, Any]) -> bool:
    """Return whether at least one executed tool produced non-error evidence."""

    return any(not key.endswith("_error") for key in facts)


def _unanswerable_response(
    *,
    request: AgentChatRequest,
    intent: str,
    tool_calls: list[str] | None = None,
    tool_results: list[AgentToolTrace] | None = None,
    references: list[str] | None = None,
    warnings: list[str] | None = None,
    performance: AgentChatPerformance | None = None,
) -> AgentChatResponse:
    """Return the fixed refusal when no accurate, grounded answer is available."""

    return AgentChatResponse(
        session_id=request.session_id,
        intent=intent,
        answer=UNANSWERABLE_TEXT,
        tool_calls=tool_calls or [],
        tool_results=tool_results or [],
        references=references or [],
        warnings=warnings or [],
        performance=performance or AgentChatPerformance(),
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_market_schedule(
    request: AgentChatRequest,
    latest_trade_date: date,
) -> AgentChatResponse:
    """Answer basic A-share market schedule questions."""

    requested_date = date.today()
    is_weekday = requested_date.weekday() < 5
    is_known_trading_day = requested_date == latest_trade_date
    if not is_weekday:
        status = (
            f"{requested_date.isoformat()} \u662f\u5468"
            f"{'\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u65e5'[requested_date.weekday()]}"
            "\uff0cA \u80a1\u901a\u5e38\u4e0d\u5f00\u76d8\u3002"
        )
    elif is_known_trading_day:
        status = f"{requested_date.isoformat()} \u5728\u672c\u5730\u6570\u636e\u4e2d\u662f\u6700\u65b0\u4ea4\u6613\u65e5\u3002"
    else:
        status = (
            f"{requested_date.isoformat()} \u662f\u5de5\u4f5c\u65e5\uff0c"
            "\u4f46\u6211\u5f53\u524d\u6ca1\u6709\u63a5\u5165\u5b8c\u6574\u4ea4\u6613\u6240\u8282\u5047\u65e5\u65e5\u5386\uff0c"
            "\u6240\u4ee5\u4e0d\u80fd\u4ec5\u51ed\u672c\u5730\u6570\u636e\u786e\u8ba4\u5f53\u5929\u662f\u5426\u5f00\u5e02\u3002"
        )

    answer = (
        f"{status}\n"
        "A \u80a1\u5e38\u89c4\u4ea4\u6613\u65f6\u95f4\u4e3a\uff1a"
        "\u4e0a\u5348 09:30-11:30\uff0c\u4e0b\u5348 13:00-15:00\u3002"
        "\u5f00\u76d8\u96c6\u5408\u7ade\u4ef7\u901a\u5e38\u4e3a 09:15-09:25\uff0c"
        "09:25-09:30 \u4e3a\u5f00\u76d8\u524d\u7684\u77ed\u6682\u95f4\u9694\u3002"
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="market_schedule",
        answer=answer,
        tool_calls=[],
        references=[f"latest_local_trade_date={latest_trade_date.isoformat()}"],
        warnings=[],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_market_context(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
) -> AgentChatResponse:
    """Answer market-overview questions with objective facts plus the LLM."""

    market_tool = tools.market_summary()
    summary = market_tool.output
    facts = {
        "trade_date": summary.trade_date.isoformat(),
        "limit_up_count": summary.limit_up_count,
        "first_board_count": summary.first_board_count,
        "continued_board_count": summary.continued_board_count,
        "failed_count": summary.failed_count,
        "failed_limit_up_rate": summary.failed_limit_up_rate,
        "max_board_height": summary.max_board_height,
        "hot_industries": summary.hot_industries,
        "hot_concepts": [item.model_dump(mode="json") for item in summary.hot_concepts],
    }
    answer, source, warnings = _generate_llm_answer(
        request=request,
        intent="market_context",
        facts=facts,
        fallback=_template_market_overview_answer(summary),
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="market_context",
        answer=answer,
        tool_calls=["market_summary", source],
        tool_results=[market_tool.trace()],
        references=[f"trade_date={summary.trade_date.isoformat()}"],
        warnings=warnings,
        generated_by=CHAT_AGENT_VERSION,
    )


def _build_session_context(
    recent_runs: list[AgentRun],
    conversation_messages: list[ChatSessionMessage] | None = None,
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
    if context.symbol is None:
        context.symbol = input_json.get("symbol")
    if context.trade_date is None:
        context.trade_date = _parse_optional_date(input_json.get("trade_date"))

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
    return []


def _parse_optional_date(value: object) -> date | None:
    """Parse a date-like value from saved JSON context."""

    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _explicit_request_trade_date(request: AgentChatRequest) -> date | None:
    """Use only a user/API date; undated questions must reach tools as latest."""

    return _extract_trade_date(request.message) or request.trade_date


def _latest_external_trade_date(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
) -> date | None:
    """Pin undated external snapshots to the latest complete local trade date.

    Some providers interpret an omitted date as their previous cached snapshot.
    The user's explicit text date still wins, while stale page context must not
    turn an undated question into a historical query.
    """

    explicit_date = _extract_trade_date(request.message)
    if explicit_date is not None:
        return explicit_date
    return max((event.trade_date for event in events), default=None)


def _parse_optional_int(value: object) -> int | None:
    """Parse an optional integer from LLM tool arguments."""

    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_bool(value: object) -> bool | None:
    """Parse an optional boolean from LLM tool arguments."""

    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _filter_query_from_context(label: str) -> _FirstBoardFilterQuery:
    """Rebuild a filter query from a saved context label."""

    return _extract_first_board_filter(label) or _FirstBoardFilterQuery(
        label=label,
        aliases=(label,),
    )


def _looks_like_first_board_data_question(message: str) -> bool:
    """Return whether a dated question asks for first-board data availability."""

    return any(
        keyword in message
        for keyword in (
            "\u9996\u677f",
            "\u6da8\u505c",
            "\u5019\u9009",
            "\u6570\u636e",
        )
    )


def _mentions_first_board_scope(message: str) -> bool:
    """Return whether the user explicitly asks about the first-board pool."""

    return "\u9996\u677f" in message or "\u5019\u9009" in message


def _looks_like_context_pool_question(message: str) -> bool:
    """Return whether text refers to a previous candidate pool."""

    return any(
        keyword in message
        for keyword in (
            "\u90a3\u91cc\u9762",
            "\u5176\u4e2d",
            "\u521a\u624d",
            "\u8fd9\u4e9b",
            "\u90a3\u4e9b",
            "\u8fd9\u4e2a\u6c60\u5b50",
            "\u5019\u9009\u91cc",
        )
    )


def _looks_like_context_date_question(message: str) -> bool:
    """Return whether an undated follow-up explicitly refers to the prior date."""

    return any(
        keyword in message
        for keyword in ("那天", "当天", "该日", "那个交易日", "这个交易日")
    )


def _looks_like_context_top_question(message: str) -> bool:
    """Return whether text asks for the best or top-scored context item."""

    return any(
        keyword in message
        for keyword in (
            "\u8bc4\u5206\u6700\u9ad8",
            "\u6700\u9ad8\u5206",
            "\u5206\u6700\u9ad8",
            "\u8c01\u6700\u9ad8",
            "\u7b2c\u4e00\u4e2a",
            "\u6392\u7b2c\u4e00",
            "\u6700\u5f3a",
        )
    )


def _looks_like_context_symbol_question(message: str) -> bool:
    """Return whether the user likely refers to a stock from prior turns."""

    return any(
        keyword in message
        for keyword in (
            "\u5b83",
            "\u8fd9\u53ea",
            "\u8be5\u80a1",
            "\u8fd9\u4e2a\u80a1",
            "\u521a\u624d\u90a3\u53ea",
            "\u4e0a\u9762\u90a3\u53ea",
        )
    )


def _looks_like_first_board_sector_question(message: str) -> bool:
    """Return whether text asks for sectors among first-board candidates."""

    return _mentions_first_board_scope(message) and _looks_like_first_board_data_question(message) and any(
        keyword in message
        for keyword in (
            "\u677f\u5757",
            "\u4e3b\u8981\u677f\u5757",
            "\u884c\u4e1a\u5206\u5e03",
            "\u884c\u4e1a",
            "\u9898\u6750\u5206\u5e03",
        )
    )


def _extract_first_board_filter(message: str) -> _FirstBoardFilterQuery | None:
    """Extract a topic or industry filter from first-board chat text."""

    normalized = message.lower()
    known_topics = (
        (
            "\u533b\u836f",
            (
                "\u533b\u836f",
                "\u533b\u7597",
                "\u5236\u836f",
                "\u836f\u4e1a",
                "\u751f\u7269",
                "\u4e2d\u836f",
                "\u5316\u5b66\u5236\u836f",
                "\u533b\u7597\u670d\u52a1",
                "\u533b\u7597\u5668\u68b0",
                "\u75ab\u82d7",
                "cro",
            ),
        ),
        (
            "\u673a\u5668\u4eba",
            ("\u673a\u5668\u4eba", "\u5de5\u4e1a\u6bcd\u673a", "\u81ea\u52a8\u5316"),
        ),
        (
            "\u4eba\u5de5\u667a\u80fd",
            ("\u4eba\u5de5\u667a\u80fd", "\u7b97\u529b", "\u6a21\u578b", "ai"),
        ),
        (
            "\u6d88\u8d39",
            ("\u6d88\u8d39", "\u98df\u54c1", "\u96f6\u552e", "\u767e\u8d27", "\u65c5\u6e38"),
        ),
        (
            "\u65b0\u80fd\u6e90",
            ("\u65b0\u80fd\u6e90", "\u9502\u7535", "\u5149\u4f0f", "\u50a8\u80fd", "\u98ce\u7535"),
        ),
    )
    for label, aliases in known_topics:
        if any(alias.lower() in normalized for alias in aliases):
            return _FirstBoardFilterQuery(label=label, aliases=aliases)

    match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,10})(?:\u76f8\u5173|\u884c\u4e1a|\u9898\u6750)",
        message,
    )
    if not match:
        return None
    raw_query = match.group(1)
    for noise in (
        "\u9996\u677f",
        "\u7684\u80a1\u7968",
        "\u80a1\u7968",
        "\u662f",
        "\u6709\u54ea\u4e9b",
    ):
        raw_query = raw_query.replace(noise, "")
    query = raw_query.strip()
    if len(query) < 2:
        return None
    return _FirstBoardFilterQuery(label=query, aliases=(query,))


def _rating_matches_filter(
    rating: FirstBoardRating,
    filter_query: _FirstBoardFilterQuery,
) -> bool:
    """Match a rating against name, industry and concept fields."""

    facts = rating.facts
    searchable = " ".join(
        (facts.name, facts.industry, facts.concept)
    ).lower()
    return any(alias.lower() in searchable for alias in filter_query.aliases)


def _answer_first_board_filter(
    request: AgentChatRequest,
    ratings_tool: ToolResult,
    filter_query: _FirstBoardFilterQuery,
) -> AgentChatResponse:
    """Answer dated first-board pool questions with a topic or industry filter."""

    ratings: FirstBoardRatingsResponse = ratings_tool.output
    matches = _filter_first_board_candidates(ratings, filter_query)
    trade_date = ratings.trade_date
    if matches:
        lines = [
            (
                f"{trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u4e2d\uff0c"
                f"{filter_query.label}\u76f8\u5173\u7684\u6709 {len(matches)} \u53ea\uff1a"
            )
        ]
        for item in matches[:10]:
            facts = item.facts
            lines.append(
                (
                    f"- {facts.name}({facts.symbol}) {item.rating}/{item.score:.1f}\uff0c"
                    f"\u884c\u4e1a\uff1a{facts.industry}\uff0c"
                    f"\u9898\u6750\uff1a{facts.concept}\uff0c"
                    f"\u9996\u5c01\uff1a{facts.first_limit_time.strftime('%H:%M')}\uff0c"
                    f"\u70b8\u677f {facts.break_count} \u6b21"
                )
            )
        if len(matches) > 10:
            lines.append(
                f"\u8fd8\u6709 {len(matches) - 10} \u53ea\u672a\u5c55\u793a\uff0c\u53ef\u4ee5\u7ee7\u7eed\u8ffd\u95ee\u8981\u5b8c\u6574\u5217\u8868\u6216\u6309\u8bc4\u5206\u6392\u5e8f\u3002"
            )
    else:
        lines = [
            (
                f"{trade_date.isoformat()} \u672c\u5730\u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u91cc\uff0c"
                f"\u6ca1\u6709\u547d\u4e2d\u201c{filter_query.label}\u201d\u7684\u80a1\u7968\u3002"
            ),
            "\u8fd9\u4e2a\u7ed3\u8bba\u53ea\u57fa\u4e8e\u5df2\u5165\u5e93\u7684\u9996\u677f\u5019\u9009\u3001\u884c\u4e1a\u548c\u9898\u6750\u5b57\u6bb5\uff0c\u4e0d\u4ee3\u8868\u5168 A \u80a1\u6240\u6709\u76f8\u5173\u80a1\u3002",
        ]

    filter_trace = _build_first_board_filter_trace(ratings, filter_query, matches)
    return AgentChatResponse(
        session_id=request.session_id,
        intent="first_board_filter",
        answer="\n".join(lines),
        tool_calls=["first_board_ratings", "first_board_filter"],
        tool_results=[ratings_tool.trace(), filter_trace],
        references=[
            f"trade_date={trade_date.isoformat()}",
            f"filter={filter_query.label}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_first_board_context_top(
    request: AgentChatRequest,
    ratings_tool: ToolResult,
    filter_query: _FirstBoardFilterQuery,
    context_symbols: list[str],
) -> AgentChatResponse:
    """Answer follow-up questions about the top stock in a previous pool."""

    ratings: FirstBoardRatingsResponse = ratings_tool.output
    matches = _filter_first_board_candidates(ratings, filter_query)
    if context_symbols:
        symbol_set = set(context_symbols)
        matches = [item for item in matches if item.facts.symbol in symbol_set]
    filter_trace = _build_first_board_filter_trace(ratings, filter_query, matches)
    trade_date = ratings.trade_date
    if not matches:
        answer = (
            "\u6211\u7406\u89e3\u4f60\u5728\u95ee\u4e0a\u4e00\u8f6e\u7684\u5019\u9009\u6c60\uff0c"
            "\u4f46\u5f53\u524d\u6ca1\u6709\u627e\u5230\u53ef\u590d\u7528\u7684\u5339\u914d\u80a1\u7968\u5217\u8868\u3002"
            "\u53ef\u4ee5\u5148\u91cd\u95ee\u4e00\u6b21\u5177\u4f53\u884c\u4e1a\u6216\u9898\u6750\u3002"
        )
        return AgentChatResponse(
            session_id=request.session_id,
            intent="first_board_context_top",
            answer=answer,
            tool_calls=["first_board_ratings", "first_board_filter"],
            tool_results=[ratings_tool.trace(), filter_trace],
            references=[f"trade_date={trade_date.isoformat()}"],
            warnings=[_safety_warning()],
            generated_by=CHAT_AGENT_VERSION,
        )

    target = matches[0]
    facts = target.facts
    answer = (
        f"\u4e0a\u4e00\u8f6e\u201c{filter_query.label}\u201d\u5019\u9009\u6c60\u91cc\uff0c"
        f"{trade_date.isoformat()} \u8bc4\u5206\u6700\u9ad8\u7684\u662f "
        f"{facts.name}({facts.symbol})\uff0c"
        f"\u8bc4\u7ea7 {target.rating}\uff0c\u8bc4\u5206 {target.score:.1f}\uff0c"
        f"\u7f6e\u4fe1\u5ea6 {target.confidence:.0%}\u3002\n"
        f"\u4e3b\u8981\u7406\u7531\uff1a{SEMI.join(target.reasons[:3])}\u3002"
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="first_board_context_top",
        answer=answer,
        tool_calls=["first_board_ratings", "first_board_filter"],
        tool_results=[ratings_tool.trace(), filter_trace],
        references=[
            f"trade_date={trade_date.isoformat()}",
            f"filter={filter_query.label}",
            f"symbol={facts.symbol}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_limit_up_query(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    trade_date: date | None,
    filter_query: _FirstBoardFilterQuery | None,
) -> AgentChatResponse:
    """Answer general same-day limit-up event questions."""

    planner_arguments = {"query": filter_query.label} if filter_query else None
    contract = build_limit_up_query_contract(
        request.message,
        request_trade_date=trade_date,
        planner_arguments=planner_arguments,
    )
    result = tools.limit_up_events(
        trade_date=contract.trade_date,
        board_height=contract.board_height,
        min_board_height=contract.min_board_height,
        highest_only=contract.highest_only,
        market=contract.market,
        query=contract.query,
        event_status=contract.event_status,
        sort_by=contract.sort_by,
        sort_order=contract.sort_order,
        limit=contract.limit,
    )
    result.input["query_contract"] = contract.to_dict()
    result.trace_output["query_contract"] = contract.to_dict()
    events: list[LimitUpEvent] = result.output

    answer = _template_limit_up_events_answer(
        request=request,
        trade_date=str(result.trace_output.get("trade_date")),
        events=events,
        board_height=contract.board_height,
        min_board_height=contract.min_board_height,
        query=contract.query,
        broken_only=contract.event_status in {"failed", "broken_intraday"},
        market=contract.market,
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="limit_up_query",
        answer=answer,
        tool_calls=["limit_up_events"],
        tool_results=[result.trace()],
        references=[f"trade_date={result.trace_output.get('trade_date')}"],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _template_limit_up_events_answer(
    *,
    request: AgentChatRequest,
    trade_date: str,
    events: list[LimitUpEvent],
    board_height: int | None,
    min_board_height: int | None,
    query: str | None,
    broken_only: bool,
    market: str | None,
) -> str:
    """Build a deterministic answer for general limit-up event queries."""

    scope = "\u6da8\u505c\u80a1"
    if board_height is not None:
        scope = f"{board_height}\u677f\u80a1"
    elif min_board_height == 2:
        scope = "\u8fde\u677f\u80a1"
    if broken_only:
        scope = "\u70b8\u677f\u80a1"
    if market:
        scope = f"{MARKET_SEGMENT_LABELS.get(market, market)}{scope}"
    if query:
        scope = f"{query}\u76f8\u5173{scope}"
    if "\u6700\u9ad8\u677f" in request.message and events:
        scope = f"\u6700\u9ad8\u677f\uff08{events[0].board_height}\u677f\uff09"

    if not events:
        return f"{trade_date} \u672c\u5730\u6570\u636e\u4e2d\u6ca1\u6709\u547d\u4e2d\u201c{scope}\u201d\u7684\u6837\u672c\u3002"

    lines = [f"{trade_date} \u672c\u5730\u6570\u636e\u4e2d\uff0c{scope}\u5171 {len(events)} \u53ea\uff1a"]
    for event in events[:20]:
        lines.append(
            (
                f"- {event.name}({event.symbol}) {event.board_height}\u677f\uff0c"
                f"\u884c\u4e1a\uff1a{event.industry}\uff0c"
                f"\u9898\u6750\uff1a{event.concept}\uff0c"
                f"\u9996\u5c01\uff1a{event.first_limit_time.strftime('%H:%M')}\uff0c"
                f"\u70b8\u677f {event.break_count} \u6b21"
            )
        )
    if len(events) > 20:
        lines.append(f"\u8fd8\u6709 {len(events) - 20} \u53ea\u672a\u5c55\u793a\u3002")
    return "\n".join(lines)


def _event_fact(event: LimitUpEvent) -> dict[str, Any]:
    """Serialize one limit-up event into compact Agent facts."""

    return {
        "symbol": event.symbol,
        "name": event.name,
        "trade_date": event.trade_date.isoformat(),
        "board_height": event.board_height,
        "industry": event.industry,
        "concept": event.concept,
        "first_limit_time": event.first_limit_time.strftime("%H:%M"),
        "last_limit_time": event.last_limit_time.strftime("%H:%M"),
        "break_count": event.break_count,
        "closed_limit": event.closed_limit,
        "amount": event.amount,
        "turnover_rate": event.turnover_rate,
    }


def _filter_first_board_candidates(
    ratings: FirstBoardRatingsResponse,
    filter_query: _FirstBoardFilterQuery,
) -> list[FirstBoardRating]:
    """Return already-ranked candidates matching a topic filter."""

    return [
        item
        for item in ratings.candidates
        if _rating_matches_filter(item, filter_query)
    ]


def _select_filtered_target(
    matches: list[FirstBoardRating],
    preferred_symbol: str | None,
) -> FirstBoardRating:
    """Pick the explicit symbol if present; otherwise choose the top score."""

    if preferred_symbol:
        explicit = _find_rating(preferred_symbol, matches)
        if explicit is not None:
            return explicit
    return matches[0]


def _build_first_board_filter_trace(
    ratings: FirstBoardRatingsResponse,
    filter_query: _FirstBoardFilterQuery,
    matches: list[FirstBoardRating],
) -> AgentToolTrace:
    """Build the compact trace for first-board filter execution."""

    return AgentToolTrace(
        name="first_board_filter",
        input={
            "trade_date": ratings.trade_date.isoformat(),
            "label": filter_query.label,
            "aliases": list(filter_query.aliases),
            "matched_symbols": [item.facts.symbol for item in matches],
        },
        summary=(
            f"\u4ece {len(ratings.candidates)} \u53ea\u9996\u677f\u5019\u9009\u4e2d"
            f"\u547d\u4e2d {len(matches)} \u53ea\u3002"
        ),
    )


def _has_events_for_date(events: list[LimitUpEvent], trade_date: date) -> bool:
    """Return whether local event data exists for a date."""

    return any(event.trade_date == trade_date for event in events)


def _answer_missing_trade_date(
    request: AgentChatRequest,
    requested_date: date,
    events: list[LimitUpEvent],
) -> AgentChatResponse:
    """Explain that a requested historical trading date is not locally cached."""

    available_dates = sorted({event.trade_date for event in events}, reverse=True)
    latest_dates = IDEOGRAPHIC_COMMA.join(
        item.isoformat() for item in available_dates[:5]
    )
    answer = (
        f"\u6211\u672c\u5730\u6682\u65f6\u6ca1\u6709 {requested_date.isoformat()} \u7684\u6da8\u505c/\u9996\u677f\u6570\u636e\u3002"
        f"\u5f53\u524d\u53ef\u7528\u7684\u6700\u8fd1\u4ea4\u6613\u65e5\u5305\u62ec\uff1a{latest_dates}\u3002"
        "\u5982\u679c\u9700\u8981\u8fd9\u4e00\u5929\uff0c\u53ef\u4ee5\u5148\u6267\u884c\u5386\u53f2\u6570\u636e\u540c\u6b65\uff0c"
        "\u7136\u540e\u6211\u518d\u57fa\u4e8e\u672c\u5730 facts \u5206\u6790\u9996\u677f\u5019\u9009\u3002"
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="data_availability",
        answer=answer,
        tool_calls=["limit_up_event_dates"],
        tool_results=[
            {
                "name": "limit_up_event_dates",
                "input": {"requested_date": requested_date.isoformat()},
                "summary": f"未找到 {requested_date.isoformat()}，最近可用日期：{latest_dates}。",
            }
        ],
        references=[f"requested_date={requested_date.isoformat()}"],
        warnings=[],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_today_summary(
    request: AgentChatRequest,
    ratings_tool: ToolResult,
) -> AgentChatResponse:
    """Summarize the latest first-board candidate pool."""

    ratings: FirstBoardRatingsResponse = ratings_tool.output
    candidates = ratings.candidates
    trade_date = ratings.trade_date
    top_items = candidates[:5]
    lines = [
        f"{trade_date.isoformat()} \u9996\u677f\u5019\u9009\u6c60\u5171\u6709 {len(candidates)} \u53ea\u5165\u6c60\u80a1\u7968\u3002",
    ]
    if top_items:
        summary = IDEOGRAPHIC_COMMA.join(
            f"{item.facts.name}({item.facts.symbol}) {item.rating}/{item.score:.1f}"
            for item in top_items
        )
        lines.append(f"\u5f53\u524d\u8bc4\u5206\u9760\u524d\u7684\u662f\uff1a{summary}\u3002")
        lines.append(
            "\u6574\u4f53\u89c2\u5bdf\uff1a\u9ad8\u5206\u4e3b\u8981\u6765\u81ea\u9996\u5c01\u65f6\u95f4\u3001\u5c01\u677f\u7a33\u5b9a\u6027\u3001\u6210\u4ea4\u989d/\u6362\u624b\u7387\u548c\u884c\u4e1a\u70ed\u5ea6\u7b49\u7ed3\u6784\u5316\u56e0\u5b50\u3002"
        )
    else:
        lines.append("\u5f53\u524d\u4ea4\u6613\u65e5\u6ca1\u6709\u6ee1\u8db3\u8fc7\u6ee4\u6761\u4ef6\u7684\u9996\u677f\u5019\u9009\u3002")

    return AgentChatResponse(
        session_id=request.session_id,
        intent="today_summary",
        answer="\n".join(lines),
        tool_calls=["first_board_ratings"],
        tool_results=[ratings_tool.trace()],
        references=[f"trade_date={trade_date.isoformat()}"],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_first_board_sector_summary(
    request: AgentChatRequest,
    ratings_tool: ToolResult,
) -> AgentChatResponse:
    """Summarize major sectors in the first-board pool via tool facts and LLM."""

    ratings: FirstBoardRatingsResponse = ratings_tool.output
    sector_rows = _summarize_first_board_industries(ratings.candidates)
    facts = {
        "trade_date": ratings.trade_date.isoformat(),
        "candidate_count": len(ratings.candidates),
        "industry_distribution": sector_rows,
        "top_candidates": [
            {
                "symbol": item.facts.symbol,
                "name": item.facts.name,
                "industry": item.facts.industry,
                "concept": item.facts.concept,
                "rating": item.rating,
                "score": item.score,
                "first_limit_time": item.facts.first_limit_time.strftime("%H:%M"),
                "break_count": item.facts.break_count,
            }
            for item in ratings.candidates[:10]
        ],
    }
    answer, source, warnings = _generate_llm_answer(
        request=request,
        intent="first_board_sector_summary",
        facts=facts,
        fallback=_template_first_board_sector_summary(ratings, sector_rows),
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="first_board_sector_summary",
        answer=answer,
        tool_calls=["first_board_ratings", source],
        tool_results=[ratings_tool.trace()],
        references=[f"trade_date={ratings.trade_date.isoformat()}"],
        warnings=[_safety_warning(), *warnings],
        generated_by=CHAT_AGENT_VERSION,
    )


def _summarize_first_board_industries(
    candidates: list[FirstBoardRating],
) -> list[dict]:
    """Aggregate first-board candidates by industry for LLM consumption."""

    grouped: dict[str, list[FirstBoardRating]] = {}
    for item in candidates:
        industry = item.facts.industry or "\u672a\u6807\u6ce8"
        grouped.setdefault(industry, []).append(item)

    rows = []
    for industry, items in grouped.items():
        top_items = sorted(items, key=lambda item: (-item.score, item.facts.first_limit_time))[:5]
        rows.append(
            {
                "industry": industry,
                "count": len(items),
                "avg_score": round(sum(item.score for item in items) / len(items), 1),
                "top_symbols": [
                    {
                        "symbol": item.facts.symbol,
                        "name": item.facts.name,
                        "rating": item.rating,
                        "score": item.score,
                    }
                    for item in top_items
                ],
            }
        )
    return sorted(rows, key=lambda item: (-item["count"], -item["avg_score"], item["industry"]))[:8]


def _template_first_board_sector_summary(
    ratings: FirstBoardRatingsResponse,
    sector_rows: list[dict],
) -> str:
    """Fallback sector answer when the LLM provider is unavailable."""

    if not sector_rows:
        return (
            f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u6682\u65e0\u5165\u6c60\u80a1\u7968\u3002"
        )
    lines = [
        (
            f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u5171 "
            f"{len(ratings.candidates)} \u53ea\uff0c\u4e3b\u8981\u677f\u5757\u5982\u4e0b\uff1a"
        )
    ]
    for row in sector_rows[:5]:
        names = IDEOGRAPHIC_COMMA.join(
            f"{item['name']}({item['symbol']}) {item['rating']}/{item['score']:.1f}"
            for item in row["top_symbols"][:3]
        )
        lines.append(
            f"- {row['industry']}\uff1a{row['count']} \u53ea\uff0c\u5e73\u5747\u5206 {row['avg_score']:.1f}\uff1b\u4ee3\u8868\uff1a{names}"
        )
    return "\n".join(lines)


def _answer_rating_explain(
    request: AgentChatRequest,
    symbol: str,
    candidates: list[FirstBoardRating],
) -> AgentChatResponse:
    """Explain why a candidate received its current rating."""

    rating = _find_rating(symbol, candidates)
    if rating is None:
        return _missing_symbol_response(request, symbol)

    breakdown = SEMI.join(
        f"{item.name}{item.score:.1f}/{item.max_score:.0f}"
        for item in rating.score_breakdown[:5]
    )
    reasons = SEMI.join(rating.reasons[:4])
    risks = SEMI.join(rating.risks[:3])
    answer = (
        f"{rating.facts.name}({rating.facts.symbol}) \u5f53\u524d\u8bc4\u7ea7\u4e3a {rating.rating}\uff0c"
        f"\u8bc4\u5206 {rating.score:.1f}\uff0c\u7f6e\u4fe1\u5ea6 {rating.confidence:.0%}\u3002\n"
        f"\u4e3b\u8981\u652f\u6301\u56e0\u7d20\uff1a{reasons}\u3002\n"
        f"\u8bc4\u5206\u62c6\u89e3\uff1a{breakdown}\u3002\n"
        f"\u9700\u8981\u89c2\u5bdf\u7684\u98ce\u9669\uff1a{risks}\u3002"
    )

    return AgentChatResponse(
        session_id=request.session_id,
        intent="rating_explain",
        answer=answer,
        tool_calls=["first_board_ratings"],
        references=[
            f"symbol={rating.facts.symbol}",
            f"trade_date={rating.facts.trade_date.isoformat()}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_risk_summary(
    request: AgentChatRequest,
    symbol: str,
    candidates: list[FirstBoardRating],
) -> AgentChatResponse:
    """Summarize risk labels for one candidate."""

    rating = _find_rating(symbol, candidates)
    if rating is None:
        return _missing_symbol_response(request, symbol)

    answer = (
        f"{rating.facts.name}({rating.facts.symbol}) \u7684\u98ce\u9669\u89c2\u5bdf\u4e3b\u8981\u662f\uff1a"
        f"{SEMI.join(rating.risks)}\u3002"
        "\u8fd9\u4e9b\u98ce\u9669\u6765\u81ea\u5df2\u8bb0\u5f55\u7684\u5c01\u677f\u8fc7\u7a0b\u3001\u6362\u624b/\u6210\u4ea4\u989d\u548c\u5f53\u65e5\u5e02\u573a\u73af\u5883\u3002"
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="risk_summary",
        answer=answer,
        tool_calls=["first_board_ratings"],
        references=[
            f"symbol={rating.facts.symbol}",
            f"trade_date={rating.facts.trade_date.isoformat()}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_general_llm(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    ratings_tool: ToolResult,
) -> AgentChatResponse:
    """Use a compact tool context for broad questions that miss fixed intents."""

    market_tool = tools.market_summary()
    summary = market_tool.output
    ratings: FirstBoardRatingsResponse = ratings_tool.output
    candidates = ratings.candidates
    facts = {
        "trade_date": summary.trade_date.isoformat(),
        "market_summary": {
            "limit_up_count": summary.limit_up_count,
            "first_board_count": summary.first_board_count,
            "continued_board_count": summary.continued_board_count,
            "failed_limit_up_rate": summary.failed_limit_up_rate,
            "max_board_height": summary.max_board_height,
            "hot_industries": summary.hot_industries,
        },
        "top_first_board_candidates": [
            {
                "symbol": item.facts.symbol,
                "name": item.facts.name,
                "rating": item.rating,
                "score": item.score,
                "confidence": item.confidence,
                "reasons": item.reasons[:3],
                "risks": item.risks[:3],
            }
            for item in candidates[:5]
        ],
    }
    answer, source, warnings = _generate_llm_answer(
        request=request,
        intent="general_llm",
        facts=facts,
        fallback=TEXT["unknown"],
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="general_llm",
        answer=answer,
        tool_calls=["market_summary", "first_board_ratings", source],
        tool_results=[market_tool.trace(), ratings_tool.trace()],
        references=[f"trade_date={summary.trade_date.isoformat()}"],
        warnings=warnings,
        generated_by=CHAT_AGENT_VERSION,
    )


def _answer_tool_grounded_question(
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    ratings_tool: ToolResult,
    filter_query: _FirstBoardFilterQuery | None,
    symbol: str | None,
    intent: str,
) -> AgentChatResponse:
    """Answer open-ended first-board questions from a reusable facts package."""

    market_tool = tools.market_summary()
    ratings: FirstBoardRatingsResponse = ratings_tool.output
    filtered_candidates = (
        _filter_first_board_candidates(ratings, filter_query)
        if filter_query
        else []
    )
    selected_rating = _find_rating(symbol, ratings.candidates) if symbol else None
    sector_rows = _summarize_first_board_industries(ratings.candidates)
    facts = _build_tool_grounded_facts(
        market_tool=market_tool,
        ratings=ratings,
        sector_rows=sector_rows,
        filtered_candidates=filtered_candidates,
        filter_query=filter_query,
        selected_rating=selected_rating,
    )
    fallback = _template_tool_grounded_answer(
        request=request,
        ratings=ratings,
        sector_rows=sector_rows,
        filtered_candidates=filtered_candidates,
        filter_query=filter_query,
        selected_rating=selected_rating,
    )
    answer, source, warnings = _generate_llm_answer(
        request=request,
        intent=intent,
        facts=facts,
        fallback=fallback,
    )

    tool_results = [market_tool.trace(), ratings_tool.trace()]
    if filter_query:
        tool_results.append(
            _build_first_board_filter_trace(
                ratings,
                filter_query,
                filtered_candidates,
            )
        )
    references = [f"trade_date={ratings.trade_date.isoformat()}"]
    if filter_query:
        references.append(f"filter={filter_query.label}")
    if selected_rating:
        references.append(f"symbol={selected_rating.facts.symbol}")

    return AgentChatResponse(
        session_id=request.session_id,
        intent=intent,
        answer=answer,
        tool_calls=["market_summary", "first_board_ratings", source],
        tool_results=tool_results,
        references=references,
        warnings=warnings,
        generated_by=CHAT_AGENT_VERSION,
    )


def _build_tool_grounded_facts(
    market_tool: ToolResult,
    ratings: FirstBoardRatingsResponse,
    sector_rows: list[dict],
    filtered_candidates: list[FirstBoardRating],
    filter_query: _FirstBoardFilterQuery | None,
    selected_rating: FirstBoardRating | None,
) -> dict:
    """Build the reusable facts package for model-generated chat answers."""

    summary: MarketSummary = market_tool.output
    return {
        "data_scope": (
            "\u672c\u5730\u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\uff0c"
            "\u5df2\u6392\u9664 ST\u3001\u5317\u4ea4\u6240\u3001\u79d1\u521b\u677f\u3001"
            "\u65b0\u80a1/\u6b21\u65b0\u548c\u6210\u4ea4\u989d\u8fc7\u5c0f\u6837\u672c"
        ),
        "trade_date": ratings.trade_date.isoformat(),
        "market_summary": {
            "limit_up_count": summary.limit_up_count,
            "first_board_count": summary.first_board_count,
            "continued_board_count": summary.continued_board_count,
            "failed_limit_up_rate": summary.failed_limit_up_rate,
            "max_board_height": summary.max_board_height,
            "hot_industries": summary.hot_industries,
        },
        "first_board_candidate_count": len(ratings.candidates),
        "top_first_board_candidates": [
            _rating_fact(item) for item in ratings.candidates[:12]
        ],
        "industry_distribution": sector_rows,
        "filter": filter_query.label if filter_query else None,
        "filtered_candidates": [
            _rating_fact(item) for item in filtered_candidates[:12]
        ],
        "selected_candidate": _rating_fact(selected_rating) if selected_rating else None,
    }


def _rating_fact(rating: FirstBoardRating | None) -> dict | None:
    """Serialize one rating into compact facts for LLM prompts."""

    if rating is None:
        return None
    facts = rating.facts
    return {
        "symbol": facts.symbol,
        "name": facts.name,
        "industry": facts.industry,
        "concept": facts.concept,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
        "first_limit_time": facts.first_limit_time.strftime("%H:%M"),
        "break_count": facts.break_count,
        "amount": facts.amount,
        "turnover_rate": facts.turnover_rate,
        "position": _rating_position_fact(rating),
        "reasons": rating.reasons[:4],
        "risks": rating.risks[:3],
    }


def _brief_rating_fact(rating: FirstBoardRating) -> dict[str, Any]:
    """Serialize enough data to list a candidate without repeating evidence."""

    facts = rating.facts
    return {
        "symbol": facts.symbol,
        "name": facts.name,
        "industry": facts.industry,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
        "position": _rating_position_fact(rating),
    }


def _rating_position_fact(rating: FirstBoardRating) -> dict[str, Any] | None:
    """Serialize one candidate's K-line position without its full metric vector."""

    enrichment = rating.facts.enrichment
    position = enrichment.position if enrichment else None
    if position is None:
        return None
    return {
        "regime": position.primary.regime,
        "label": position.primary.label,
        "match_score": position.primary.score,
        "confidence": position.confidence,
        "tags": position.tags[:3],
    }


def _template_tool_grounded_answer(
    request: AgentChatRequest,
    ratings: FirstBoardRatingsResponse,
    sector_rows: list[dict],
    filtered_candidates: list[FirstBoardRating],
    filter_query: _FirstBoardFilterQuery | None,
    selected_rating: FirstBoardRating | None,
) -> str:
    """Fallback answer for open-ended tool-grounded questions."""

    message = request.message
    if filter_query:
        return _template_filtered_candidate_answer(ratings, filter_query, filtered_candidates)
    if selected_rating:
        return _template_selected_rating_answer(selected_rating)
    if _looks_like_first_board_position_question(message):
        return _template_first_board_position_answer(_compact_ratings_facts(ratings))
    if _looks_like_first_board_sector_question(message):
        return _template_first_board_sector_summary(ratings, sector_rows)
    if _looks_like_top_candidate_question(message):
        return _template_top_candidate_answer(ratings)
    return _template_today_summary_answer(ratings)


def _looks_like_top_candidate_question(message: str) -> bool:
    """Return whether the user asks for top-scored candidates."""

    return any(
        keyword in message
        for keyword in (
            "\u8bc4\u5206\u9760\u524d",
            "\u9ad8\u5206",
            "\u524d\u51e0",
            "top",
            "\u6392\u540d",
            "\u5019\u9009",
        )
    )


def _template_top_candidate_answer(ratings: FirstBoardRatingsResponse) -> str:
    """Fallback answer for top first-board candidate questions."""

    top_items = ratings.candidates[:8]
    if not top_items:
        return f"{ratings.trade_date.isoformat()} \u6ca1\u6709\u9996\u677f\u8bc4\u7ea7\u5165\u6c60\u5019\u9009\u3002"
    lines = [
        (
            f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u4e2d\uff0c"
            f"\u8bc4\u5206\u9760\u524d\u7684\u5019\u9009\u6709\uff1a"
        )
    ]
    for item in top_items:
        facts = item.facts
        lines.append(
            (
                f"- {facts.name}({facts.symbol}) {item.rating}/{item.score:.1f}\uff0c"
                f"\u884c\u4e1a\uff1a{facts.industry}\uff0c"
                f"\u9996\u5c01\uff1a{facts.first_limit_time.strftime('%H:%M')}\uff0c"
                f"\u70b8\u677f {facts.break_count} \u6b21"
            )
        )
    return "\n".join(lines)


def _template_today_summary_answer(ratings: FirstBoardRatingsResponse) -> str:
    """Fallback broad summary from first-board ratings."""

    sector_rows = _summarize_first_board_industries(ratings.candidates)
    sector_text = IDEOGRAPHIC_COMMA.join(
        f"{row['industry']} {row['count']} \u53ea" for row in sector_rows[:3]
    )
    top_text = IDEOGRAPHIC_COMMA.join(
        f"{item.facts.name}({item.facts.symbol}) {item.rating}/{item.score:.1f}"
        for item in ratings.candidates[:5]
    )
    return (
        f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u5171 "
        f"{len(ratings.candidates)} \u53ea\u3002"
        f"\u4e3b\u8981\u677f\u5757\uff1a{sector_text or '\u6682\u65e0'}\u3002"
        f"\u8bc4\u5206\u9760\u524d\uff1a{top_text or '\u6682\u65e0'}\u3002"
    )


def _template_filtered_candidate_answer(
    ratings: FirstBoardRatingsResponse,
    filter_query: _FirstBoardFilterQuery,
    candidates: list[FirstBoardRating],
) -> str:
    """Fallback answer for topic-filtered candidate questions."""

    if not candidates:
        return (
            f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u91cc"
            f"\u6ca1\u6709\u547d\u4e2d\u201c{filter_query.label}\u201d\u7684\u80a1\u7968\u3002"
        )
    lines = [
        (
            f"{ratings.trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u91cc\uff0c"
            f"{filter_query.label}\u76f8\u5173\u5019\u9009\u6709 {len(candidates)} \u53ea\uff1a"
        )
    ]
    for item in candidates[:8]:
        lines.append(
            f"- {item.facts.name}({item.facts.symbol}) {item.rating}/{item.score:.1f}\uff0c\u884c\u4e1a\uff1a{item.facts.industry}"
        )
    return "\n".join(lines)


def _template_selected_rating_answer(rating: FirstBoardRating) -> str:
    """Fallback answer for a selected candidate."""

    return (
        f"{rating.facts.name}({rating.facts.symbol}) \u5f53\u524d\u8bc4\u7ea7 {rating.rating}\uff0c"
        f"\u8bc4\u5206 {rating.score:.1f}\uff0c\u7f6e\u4fe1\u5ea6 {rating.confidence:.0%}\u3002"
        f"\u4e3b\u8981\u7406\u7531\uff1a{SEMI.join(rating.reasons[:3])}\u3002"
        f"\u98ce\u9669\u89c2\u5bdf\uff1a{SEMI.join(rating.risks[:2])}\u3002"
    )


def _answer_llm_explanation(
    request: AgentChatRequest,
    symbol: str,
    ratings,
    tools: AgentToolRegistry,
) -> AgentChatResponse:
    """Generate a detailed explanation from structured rating facts."""

    del tools

    rating = _find_rating(symbol, ratings.candidates)
    if rating is None:
        return _missing_symbol_response(request, symbol)

    explanation = explain_first_board_rating(rating=rating)
    tool_calls = ["first_board_ratings", *explanation.tool_calls]

    return AgentChatResponse(
        session_id=request.session_id,
        intent="llm_explanation",
        answer=explanation.answer,
        tool_calls=tool_calls,
        tool_results=[],
        references=[
            f"symbol={rating.facts.symbol}",
            f"trade_date={rating.facts.trade_date.isoformat()}",
            f"explanation_source={explanation.source}",
        ],
        warnings=[_safety_warning(), *explanation.warnings],
        generated_by=CHAT_AGENT_VERSION,
    )


def _detect_intent(message: str, intent_hint: str | None = None) -> str:
    """Map common questions to a supported tool intent."""

    if intent_hint in SUPPORTED_INTENTS:
        return intent_hint

    normalized = message.strip().lower()
    if _looks_like_capability_question(normalized):
        return "capability_intro"
    if _looks_like_unsafe_investment_question(normalized):
        return "unsafe_investment_advice"
    if any(keyword in normalized for keyword in KEYWORDS["greeting"]):
        return "greeting"
    if _looks_like_smalltalk(normalized):
        return "smalltalk"
    for intent in (
        "greeting",
        "market_schedule",
        "market_context",
        "limit_up_query",
        "risk_summary",
        "llm_explanation",
        "rating_explain",
        "limit_up_query",
        "first_board_filter",
        "first_board_sector_summary",
        "today_summary",
    ):
        if any(keyword in normalized for keyword in KEYWORDS[intent]):
            return intent
    if _looks_like_domain_question(normalized):
        return "unknown"
    return "out_of_scope"


def _looks_like_capability_question(message: str) -> bool:
    """Return whether the user asks what the Agent can do."""

    return any(keyword in message for keyword in KEYWORDS["capability_intro"])


def _looks_like_smalltalk(message: str) -> bool:
    """Return whether the message is conversational but not data seeking."""

    return message in KEYWORDS["greeting"] or any(
        keyword == message for keyword in KEYWORDS["smalltalk"]
    )


def _looks_like_unsafe_investment_question(message: str) -> bool:
    """Return whether the user asks for direct investment instructions."""

    return any(
        keyword in message
        for keyword in (
            "\u4e70\u4e0d\u4e70",
            "\u80fd\u4e0d\u80fd\u4e70",
            "\u8981\u4e0d\u8981\u4e70",
            "\u53ef\u4ee5\u4e70",
            "\u4e70\u5165",
            "\u5356\u51fa",
            "\u4ed3\u4f4d",
            "\u51e0\u6210\u4ed3",
            "\u76ee\u6807\u4ef7",
            "\u80fd\u6da8\u5230",
            "\u4f1a\u6da8\u5417",
        )
    )


def _looks_like_domain_question(message: str) -> bool:
    """Return whether a broad question belongs to the current stock-agent domain."""

    return any(
        keyword in message
        for keyword in (
            "a\u80a1",
            "\u80a1",
            "\u9996\u677f",
            "\u8fde\u677f",
            "\u6da8\u505c",
            "\u5019\u9009",
            "\u8bc4\u5206",
            "\u8bc4\u7ea7",
            "\u677f\u5757",
            "\u884c\u4e1a",
            "\u9898\u6750",
            "\u5e02\u573a",
            "\u60c5\u7eea",
            "\u98ce\u9669",
            "\u76f8\u4f3c",
            "\u6848\u4f8b",
        )
    ) or _extract_symbol_hint(message) is not None


def _looks_like_general_limit_up_question(message: str) -> bool:
    """Return whether the user asks for same-day limit-up event lists."""

    normalized = message.lower()
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


def _extract_board_height(message: str) -> int | None:
    """Extract requested board height from Chinese limit-up phrases."""

    board_height, _min_board_height = contract_board_filters(message)
    return board_height


def _limit_up_query_arguments_from_message(request: AgentChatRequest) -> dict[str, Any]:
    """Build limit-up event tool arguments from a user question."""

    filter_query = _extract_first_board_filter(request.message)
    return build_limit_up_query_contract(
        request.message,
        request_trade_date=request.trade_date,
        planner_arguments={"query": filter_query.label} if filter_query else None,
    ).to_tool_arguments()


def _generate_llm_answer(
    request: AgentChatRequest,
    intent: str,
    facts: dict,
    fallback: str,
) -> tuple[str, str, list[str]]:
    """Ask the configured LLM to answer from tool facts, with fallback."""

    if _template_answer_forced():
        return fallback, "template_general_answer", [_safety_warning()]

    system_prompt = (
        "You are LimitUpLab's A-share first-board research agent. "
        "Answer in Chinese. Use only the provided tool facts. "
        "Do not assign categorical market-sentiment labels; use objective counts, rates and index changes. "
        "If facts do not directly and sufficiently support the question, output exactly "
        "'抱歉，该问题无法回答' and nothing else. "
        "Do not provide buy/sell instructions, target prices, positions, or return promises. "
        "Keep the answer concise and practical."
    )
    user_prompt = (
        f"User question: {request.message}\n"
        f"Intent: {intent}\n"
        f"Tool facts: {facts}\n"
    )
    try:
        result = get_llm_provider().generate(system_prompt, user_prompt)
        answer = _ensure_safety_boundary(result.content)
        if _contains_forbidden_terms(answer):
            return fallback, "template_general_answer", [
                "LLM output failed safety validation; template fallback used.",
            ]
        return answer, "llm_general_answer", [_safety_warning()]
    except Exception as error:
        return fallback, "template_general_answer", [
            _safety_warning(),
            f"LLM unavailable; template fallback used: {error}",
        ]


def _template_market_overview_answer(summary) -> str:
    """Build a deterministic answer from objective market facts."""

    return (
        f"{summary.trade_date.isoformat()} \u672c\u5730\u6570\u636e\u663e\u793a\uff0c"
        f"\u5f53\u65e5\u6da8\u505c {summary.limit_up_count} \u53ea\uff0c"
        f"\u9996\u677f {summary.first_board_count} \u53ea\uff0c"
        f"\u8fde\u677f {summary.continued_board_count} \u53ea\uff0c"
        f"\u70b8\u677f\u7387 {summary.failed_limit_up_rate:.0%}\uff0c"
        f"\u6700\u9ad8\u8fde\u677f {summary.max_board_height} \u677f\u3002"
        "\u8fd9\u662f\u57fa\u4e8e\u672c\u5730\u6da8\u505c\u6570\u636e\u7684\u590d\u76d8\u5224\u65ad\uff0c"
        "\u4e0d\u5305\u542b\u5b9e\u65f6\u65b0\u95fb\u6216\u5168\u5e02\u6210\u4ea4\u989d\u6570\u636e\u3002"
    )


def _contains_forbidden_terms(content: str) -> bool:
    """Return whether content crosses product safety boundaries."""

    forbidden_terms = ("\u4e70\u5165", "\u5356\u51fa", "\u4ed3\u4f4d", "\u76ee\u6807\u4ef7", "\u6536\u76ca\u627f\u8bfa")
    return any(term in content for term in forbidden_terms)


def _looks_like_retired_case_retrieval_question(message: str) -> bool:
    """Recognize only explicit requests for the retired case-retrieval feature."""

    normalized = message.lower()
    return any(
        term in normalized
        for term in ("相似案例", "相似股票", "历史相似", "similar case", "similar stock")
    )


def _ensure_explicit_symbol_mentioned(request: AgentChatRequest, answer: str) -> str:
    """Preserve an explicitly requested stock symbol in final LLM answers."""

    symbol = request.symbol or _extract_symbol_hint(request.message)
    if not symbol or symbol in answer:
        return answer
    if _looks_like_rating_explain_question(request.message):
        return f"关于 {symbol}：\n{answer}"
    return answer


def _ensure_safety_boundary(content: str) -> str:
    """Append the safety boundary when omitted."""

    boundary = "\u4e0d\u6784\u6210\u4e70\u5356\u5efa\u8bae"
    if boundary in content:
        return content
    return f"{content.rstrip()}\n{boundary}\u3002"


def _resolve_symbol(
    message: str,
    context_symbol: str | None,
    candidates: list[FirstBoardRating],
) -> str | None:
    """Resolve a stock symbol from message text, context, or exact stock name."""

    normalized = message.replace("\uff0c", " ").replace("\uff1f", " ")
    for token in normalized.split():
        if len(token) == 6 and token.isdigit():
            return token

    for item in candidates:
        if item.facts.symbol in message or item.facts.name in message:
            return item.facts.symbol

    return context_symbol


def _find_rating(
    symbol: str,
    candidates: list[FirstBoardRating],
) -> FirstBoardRating | None:
    """Find a rating by symbol."""

    return next((item for item in candidates if item.facts.symbol == symbol), None)


def _missing_symbol_response(
    request: AgentChatRequest,
    symbol: str,
) -> AgentChatResponse:
    """Return a grounded fallback when the requested symbol is not in the pool."""

    return AgentChatResponse(
        session_id=request.session_id,
        intent="symbol_not_found",
        answer=f"\u6ca1\u6709\u5728\u5f53\u524d\u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u4e2d\u627e\u5230 {symbol}\uff0c\u56e0\u6b64\u4e0d\u80fd\u57fa\u4e8e\u672c\u5de5\u5177\u89e3\u91ca\u5b83\u7684\u8bc4\u5206\u3002",
        tool_calls=["first_board_ratings"],
        references=[f"symbol={symbol}"],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


def _safety_warning() -> str:
    """Return the standard product boundary for chat answers."""

    return TEXT["safety"]
