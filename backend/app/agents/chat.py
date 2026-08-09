"""Tool-grounded first-board chat agent."""

import re
from datetime import date

from app.agents.explanation import explain_first_board_rating
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentToolTrace,
    AgentRun,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import get_llm_provider


CHAT_AGENT_VERSION = "first-board-chat-rule-v1"
SEMI = "\uff1b"
IDEOGRAPHIC_COMMA = "\u3001"
SUPPORTED_INTENTS = {
    "greeting",
    "market_schedule",
    "market_context",
    "general_llm",
    "similar_cases",
    "risk_summary",
    "rating_explain",
    "first_board_filter",
    "first_board_filter_similar",
    "today_summary",
    "llm_explanation",
}

TEXT = {
    "greeting": "\u4f60\u597d\uff0c\u6211\u662f LimitUpLab \u7684\u9996\u677f Agent\u3002\u6211\u53ef\u4ee5\u5e2e\u4f60\u603b\u7ed3\u4eca\u5929\u9996\u677f\u3001\u89e3\u91ca\u4e2a\u80a1\u8bc4\u5206\u3001\u68c0\u7d22\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\uff0c\u4e5f\u53ef\u4ee5\u5bf9\u5f53\u524d\u4e2a\u80a1\u505a\u8be6\u7ec6\u89e3\u91ca\u3002",
    "unknown": "\u6211\u73b0\u5728\u53ef\u4ee5\u57fa\u4e8e\u9996\u677f\u8bc4\u7ea7\u3001\u8bc4\u5206\u62c6\u89e3\u548c\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\u56de\u7b54\u3002\u4f60\u53ef\u4ee5\u95ee\uff1a\u603b\u7ed3\u4eca\u5929\u9996\u677f\u3001\u4e3a\u4ec0\u4e48\u67d0\u53ea\u80a1\u7968\u8bc4\u5206\u9ad8\u3001\u4e3b\u8981\u98ce\u9669\u662f\u4ec0\u4e48\uff0c\u6216\u8005\u67d0\u53ea\u80a1\u7968\u6709\u6ca1\u6709\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\u3002",
    "safety": "\u4ee5\u4e0a\u4e3a\u57fa\u4e8e\u672c\u5730\u7ed3\u6784\u5316\u6570\u636e\u7684\u590d\u76d8\u5206\u6790\uff0c\u4e0d\u6784\u6210\u4e70\u5356\u5efa\u8bae\u3002",
}

KEYWORDS = {
    "greeting": ("\u4f60\u597d", "\u55e8", "hello", "hi"),
    "market_schedule": ("\u5f00\u76d8", "\u6536\u76d8", "\u96c6\u5408\u7ade\u4ef7", "\u4ea4\u6613\u65f6\u95f4", "open", "close"),
    "market_context": ("\u5e02\u573a", "\u60c5\u7eea", "\u8d5a\u94b1\u6548\u5e94", "\u4e8f\u94b1\u6548\u5e94", "\u6c1b\u56f4", "sentiment", "market"),
    "similar_cases": ("\u76f8\u4f3c", "\u5386\u53f2", "\u6848\u4f8b", "similar"),
    "risk_summary": ("\u98ce\u9669", "\u7f3a\u70b9", "\u95ee\u9898", "risk"),
    "llm_explanation": ("\u8be6\u7ec6", "\u89e3\u91ca", "\u5206\u6790", "explain"),
    "rating_explain": ("\u4e3a\u4ec0\u4e48", "\u8bc4\u5206", "\u8bc4\u7ea7", "\u9ad8\u5206", "\u4f4e\u5206", "score"),
    "first_board_filter": ("\u76f8\u5173", "\u884c\u4e1a", "\u9898\u6750", "\u533b\u836f", "\u533b\u7597", "\u5236\u836f", "\u836f\u4e1a", "\u751f\u7269"),
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


def answer_first_board_chat(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository | None = None,
    recent_runs: list[AgentRun] | None = None,
) -> AgentChatResponse:
    """Answer a user question by routing to deterministic first-board tools."""

    active_repository = repository or SQLiteFirstBoardRepository()
    tools = AgentToolRegistry(events=events, first_board_repository=active_repository)
    context = _build_session_context(recent_runs or [])
    plan = _build_agent_plan(request=request, context=context)
    intent = plan.intent
    trade_date = plan.trade_date
    first_board_filter = plan.filter_query

    if intent == "greeting":
        return _with_plan_trace(_answer_greeting(request), plan)
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

    ratings_tool = tools.first_board_ratings(trade_date=trade_date)
    ratings = ratings_tool.output
    symbol = _resolve_symbol(
        message=request.message,
        context_symbol=plan.symbol or request.symbol or context.symbol,
        candidates=ratings.candidates,
    )
    plan.symbol = symbol or plan.symbol

    if intent == "market_context":
        return _with_plan_trace(_answer_market_context(request, tools), plan)
    if intent == "llm_explanation" and symbol:
        return _with_plan_trace(
            _answer_llm_explanation(request, symbol, ratings, tools),
            plan,
        )
    if intent == "first_board_filter_similar":
        if first_board_filter is None:
            first_board_filter = _FirstBoardFilterQuery(
                label="\u7528\u6237\u95ee\u53e5",
                aliases=(request.message.strip(),),
            )
            plan.filter_query = first_board_filter
        return _with_plan_trace(
            _answer_first_board_filter_similar(
                request=request,
                ratings_tool=ratings_tool,
                filter_query=first_board_filter,
                tools=tools,
                preferred_symbol=symbol,
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
    if intent == "similar_cases" and symbol:
        return _with_plan_trace(
            _answer_similar_cases(request, symbol, ratings.trade_date, tools),
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
        return _with_plan_trace(_answer_today_summary(request, ratings_tool), plan)
    if symbol:
        return _with_plan_trace(
            _answer_rating_explain(request, symbol, ratings.candidates),
            plan,
        )

    return _with_plan_trace(_answer_general_llm(request, tools, ratings_tool), plan)


class _SessionContext:
    """Minimal chat context recovered from recent Agent runs."""

    def __init__(self, symbol: str | None = None, trade_date: date | None = None):
        self.symbol = symbol
        self.trade_date = trade_date


def _build_agent_plan(
    request: AgentChatRequest,
    context: _SessionContext,
) -> _AgentPlan:
    """Plan intent and tool steps from the user's natural-language question."""

    parsed_trade_date = _extract_trade_date(request.message)
    trade_date = request.trade_date or parsed_trade_date or context.trade_date
    filter_query = _extract_first_board_filter(request.message)
    intent = _detect_intent(request.message, request.intent_hint)
    if (
        parsed_trade_date
        and _looks_like_first_board_data_question(request.message)
        and filter_query is None
    ):
        intent = "today_summary"
    if filter_query and _looks_like_first_board_data_question(request.message):
        intent = "first_board_filter"
    if (
        filter_query
        and _looks_like_first_board_data_question(request.message)
        and _looks_like_similar_question(request.message)
    ):
        intent = "first_board_filter_similar"

    symbol = request.symbol or _extract_symbol_hint(request.message) or context.symbol
    tool_steps = _plan_tool_steps(intent=intent, trade_date=trade_date, symbol=symbol)
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
) -> list[dict]:
    """Return the deterministic tools needed for an intent."""

    dated_input = {"trade_date": trade_date.isoformat() if trade_date else None}
    if intent == "greeting":
        return []
    if intent == "market_schedule":
        return [{"name": "market_summary", "input": {}}]
    if intent == "market_context":
        return [{"name": "market_summary", "input": {}}]
    if intent == "first_board_filter":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {
                "name": "first_board_filter",
                "input": {"fields": ["name", "industry", "concept"]},
            },
        ]
    if intent == "first_board_filter_similar":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {
                "name": "first_board_filter",
                "input": {"fields": ["name", "industry", "concept"]},
            },
            {
                "name": "first_board_similar_cases",
                "input": {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat() if trade_date else None,
                    "limit": 5,
                },
            },
        ]
    if intent == "similar_cases":
        return [
            {"name": "first_board_ratings", "input": dated_input},
            {
                "name": "first_board_similar_cases",
                "input": {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat() if trade_date else None,
                    "limit": 5,
                },
            },
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
    if plan.symbol is not None:
        for step in plan.tool_steps:
            if step["name"] == "first_board_similar_cases":
                step["input"]["symbol"] = plan.symbol
    response.tool_results = [plan.trace(), *response.tool_results]
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
    """Answer market sentiment questions with market-summary facts plus LLM."""

    market_tool = tools.market_summary()
    summary = market_tool.output
    facts = {
        "trade_date": summary.trade_date.isoformat(),
        "sentiment": summary.sentiment,
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
        fallback=_template_market_context_answer(summary),
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


def _build_session_context(recent_runs: list[AgentRun]) -> _SessionContext:
    """Recover the latest symbol and trade date from successful session runs."""

    for run in recent_runs:
        if run.status != "success" or not run.input_json:
            continue
        symbol = run.input_json.get("symbol")
        trade_date_value = run.input_json.get("trade_date")
        trade_date = date.fromisoformat(trade_date_value) if trade_date_value else None
        if symbol or trade_date:
            return _SessionContext(symbol=symbol, trade_date=trade_date)
    return _SessionContext()


def _extract_trade_date(message: str) -> date | None:
    """Extract a trade date from common Chinese shorthand date expressions."""

    normalized = message.strip()
    full_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", normalized)
    if full_match:
        year, month, day = (int(part) for part in full_match.groups())
        return _safe_date(year, month, day)

    short_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日|号)?", normalized)
    if short_match:
        month, day = (int(part) for part in short_match.groups())
        return _safe_date(date.today().year, month, day)

    return None


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


def _looks_like_similar_question(message: str) -> bool:
    """Return whether a question asks for historical similar cases."""

    return any(keyword in message for keyword in KEYWORDS["similar_cases"])


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


def _answer_first_board_filter_similar(
    request: AgentChatRequest,
    ratings_tool: ToolResult,
    filter_query: _FirstBoardFilterQuery,
    tools: AgentToolRegistry,
    preferred_symbol: str | None,
) -> AgentChatResponse:
    """Filter first-board candidates, choose a target, then retrieve similar cases."""

    ratings: FirstBoardRatingsResponse = ratings_tool.output
    matches = _filter_first_board_candidates(ratings, filter_query)
    filter_trace = _build_first_board_filter_trace(ratings, filter_query, matches)
    trade_date = ratings.trade_date
    if not matches:
        answer = (
            f"{trade_date.isoformat()} \u9996\u677f\u8bc4\u7ea7\u5019\u9009\u6c60\u91cc\uff0c"
            f"\u6ca1\u6709\u547d\u4e2d\u201c{filter_query.label}\u201d\u7684\u80a1\u7968\uff0c"
            "\u6240\u4ee5\u65e0\u6cd5\u7ee7\u7eed\u68c0\u7d22\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\u3002"
        )
        return AgentChatResponse(
            session_id=request.session_id,
            intent="first_board_filter_similar",
            answer=answer,
            tool_calls=["first_board_ratings", "first_board_filter"],
            tool_results=[ratings_tool.trace(), filter_trace],
            references=[
                f"trade_date={trade_date.isoformat()}",
                f"filter={filter_query.label}",
            ],
            warnings=[_safety_warning()],
            generated_by=CHAT_AGENT_VERSION,
        )

    target = _select_filtered_target(matches, preferred_symbol)
    try:
        similar_tool = tools.similar_cases(
            symbol=target.facts.symbol,
            trade_date=trade_date,
            limit=5,
        )
        similar_response = similar_tool.output
    except ValueError:
        answer = (
            f"{trade_date.isoformat()} \u9996\u677f\u4e2d\uff0c"
            f"{filter_query.label}\u76f8\u5173\u5019\u9009\u5171 {len(matches)} \u53ea\u3002"
            f"\u6309\u8bc4\u5206\u9009\u62e9 {target.facts.name}({target.facts.symbol}) "
            f"\u4f5c\u4e3a\u76f8\u4f3c\u6848\u4f8b\u68c0\u7d22\u76ee\u6807\uff0c"
            f"\u5176\u8bc4\u7ea7 {target.rating}\uff0c\u8bc4\u5206 {target.score:.1f}\u3002\n"
            "\u4f46\u672c\u5730\u5c1a\u672a\u627e\u5230\u8fd9\u53ea\u80a1\u7968\u5bf9\u5e94\u7684\u9996\u677f\u7279\u5f81\u7f13\u5b58\uff0c"
            "\u56e0\u6b64\u8fd8\u4e0d\u80fd\u7ed9\u51fa\u53ef\u9a8c\u8bc1\u7684\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\u3002"
        )
        return AgentChatResponse(
            session_id=request.session_id,
            intent="first_board_filter_similar",
            answer=answer,
            tool_calls=["first_board_ratings", "first_board_filter", "first_board_similar_cases"],
            tool_results=[ratings_tool.trace(), filter_trace],
            references=[
                f"trade_date={trade_date.isoformat()}",
                f"filter={filter_query.label}",
                f"symbol={target.facts.symbol}",
            ],
            warnings=[_safety_warning()],
            generated_by=CHAT_AGENT_VERSION,
        )

    case_lines = [
        (
            f"- {item.name}({item.symbol}) {item.trade_date.isoformat()}\uff0c"
            f"\u76f8\u4f3c\u5ea6 {item.similarity:.0%}\uff0c"
            f"\u76f8\u4f3c\u70b9\uff1a{SEMI.join(item.reasons[:2]) or '\u7ed3\u6784\u7279\u5f81\u63a5\u8fd1'}"
        )
        for item in similar_response.cases[:5]
    ]
    similar_text = (
        "\n".join(case_lines)
        if case_lines
        else "\u6682\u672a\u627e\u5230\u8db3\u591f\u76f8\u4f3c\u7684\u5386\u53f2\u9996\u677f\u6837\u672c\u3002"
    )
    answer = (
        f"{trade_date.isoformat()} \u9996\u677f\u4e2d\uff0c"
        f"{filter_query.label}\u76f8\u5173\u5019\u9009\u5171 {len(matches)} \u53ea\u3002"
        f"\u6211\u5148\u6309\u8bc4\u5206\u9009\u51fa {target.facts.name}({target.facts.symbol}) "
        f"\u4f5c\u4e3a\u68c0\u7d22\u76ee\u6807\uff0c\u5176\u8bc4\u7ea7 {target.rating}\uff0c"
        f"\u8bc4\u5206 {target.score:.1f}\u3002\n"
        f"\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b\uff1a\n{similar_text}"
    )
    return AgentChatResponse(
        session_id=request.session_id,
        intent="first_board_filter_similar",
        answer=answer,
        tool_calls=["first_board_ratings", "first_board_filter", "first_board_similar_cases"],
        tool_results=[ratings_tool.trace(), filter_trace, similar_tool.trace()],
        references=[
            f"trade_date={trade_date.isoformat()}",
            f"filter={filter_query.label}",
            f"symbol={target.facts.symbol}",
        ],
        warnings=[_safety_warning()],
        generated_by=CHAT_AGENT_VERSION,
    )


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
        },
        summary=(
            f"\u4ece {len(ratings.candidates)} \u53ea\u9996\u677f\u5019\u9009\u4e2d"
            f"\u547d\u4e2d {len(matches)} \u53ea\u3002"
        ),
    )


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Build a date, returning None for invalid user text."""

    try:
        return date(year, month, day)
    except ValueError:
        return None


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


def _answer_similar_cases(
    request: AgentChatRequest,
    symbol: str,
    trade_date: date,
    tools: AgentToolRegistry,
) -> AgentChatResponse:
    """Retrieve and summarize historical similar first-board cases."""

    try:
        similar_tool = tools.similar_cases(symbol=symbol, trade_date=trade_date, limit=5)
        response = similar_tool.output
    except ValueError:
        return _missing_symbol_response(request, symbol)

    if not response.cases:
        answer = f"{symbol} \u6682\u672a\u627e\u5230\u8db3\u591f\u76f8\u4f3c\u7684\u5386\u53f2\u9996\u677f\u6837\u672c\u3002"
    else:
        case_lines = [
            (
                f"{item.name}({item.symbol}) {item.trade_date.isoformat()}\uff0c"
                f"\u76f8\u4f3c\u5ea6 {item.similarity:.0%}\uff0c"
                f"\u76f8\u4f3c\u70b9\uff1a{SEMI.join(item.reasons[:2]) or '\u7ed3\u6784\u7279\u5f81\u63a5\u8fd1'}"
            )
            for item in response.cases[:5]
        ]
        answer = (
            f"{symbol} \u7684\u5386\u53f2\u76f8\u4f3c\u6848\u4f8b Top {len(response.cases)}\uff1a\n"
            + "\n".join(f"- {line}" for line in case_lines)
        )

    return AgentChatResponse(
        session_id=request.session_id,
        intent="similar_cases",
        answer=answer,
        tool_calls=["first_board_similar_cases"],
        tool_results=[similar_tool.trace()],
        references=[
            f"symbol={symbol}",
            f"trade_date={trade_date.isoformat()}",
            f"window_days={response.window_days}" if response.cases else "window_days=unknown",
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
            "sentiment": summary.sentiment,
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


def _answer_llm_explanation(
    request: AgentChatRequest,
    symbol: str,
    ratings,
    tools: AgentToolRegistry,
) -> AgentChatResponse:
    """Generate a detailed explanation from rating facts and similar cases."""

    rating = _find_rating(symbol, ratings.candidates)
    if rating is None:
        return _missing_symbol_response(request, symbol)

    try:
        similar_tool = tools.similar_cases(
            symbol=symbol,
            trade_date=ratings.trade_date,
            limit=5,
        )
        similar_cases = similar_tool.output.cases
    except ValueError:
        similar_tool = None
        similar_cases = []

    explanation = explain_first_board_rating(rating=rating, similar_cases=similar_cases)
    tool_calls = ["first_board_ratings"]
    if similar_cases:
        tool_calls.append("first_board_similar_cases")
    tool_calls.extend(explanation.tool_calls)

    return AgentChatResponse(
        session_id=request.session_id,
        intent="llm_explanation",
        answer=explanation.answer,
        tool_calls=tool_calls,
        tool_results=[similar_tool.trace()] if similar_tool else [],
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
    for intent in (
        "greeting",
        "market_schedule",
        "market_context",
        "similar_cases",
        "risk_summary",
        "llm_explanation",
        "rating_explain",
        "first_board_filter",
        "today_summary",
    ):
        if any(keyword in normalized for keyword in KEYWORDS[intent]):
            return intent
    return "unknown"


def _generate_llm_answer(
    request: AgentChatRequest,
    intent: str,
    facts: dict,
    fallback: str,
) -> tuple[str, str, list[str]]:
    """Ask the configured LLM to answer from tool facts, with fallback."""

    system_prompt = (
        "You are LimitUpLab's A-share first-board research agent. "
        "Answer in Chinese. Use only the provided tool facts. "
        "If facts are insufficient, say what is missing. "
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


def _template_market_context_answer(summary) -> str:
    """Build a deterministic market-sentiment answer."""

    sentiment_labels = {
        "heating": "\u5347\u6e29",
        "diverging": "\u5206\u6b67",
        "cooling": "\u9000\u6f6e",
    }
    return (
        f"{summary.trade_date.isoformat()} \u672c\u5730\u6570\u636e\u663e\u793a\uff0c"
        f"A \u80a1\u77ed\u7ebf\u60c5\u7eea\u5904\u4e8e"
        f"{sentiment_labels.get(summary.sentiment, summary.sentiment)}\u72b6\u6001\u3002"
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
