"""Tool exposure profiles and their schema budget."""

import json
from types import SimpleNamespace

from app.agents.runner.gateway import BusinessToolGateway
from app.agents.runner.runner import AgentRunner
from app.agents.runner.tool_view import business_definitions
from app.agents.result_paging import READ_RESULT_DEFINITION
from app.agents.tool_profiles import PROFILE_TOOLS, resolve_tool_profile
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest


def _registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        events=[],
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=SimpleNamespace(),
    )


def _request(message: str, *, symbol: str | None = None) -> AgentChatRequest:
    return AgentChatRequest(
        session_id="profile-test",
        message=message,
        symbol=symbol,
        page_context={"page": "stock_detail"} if symbol else {"page": "dashboard"},
    )


def _definitions_chars(allowed=None) -> int:
    gateway = BusinessToolGateway(_registry())
    definitions = business_definitions(gateway, allowed=allowed)
    definitions.append(READ_RESULT_DEFINITION)
    return len(json.dumps(
        definitions, ensure_ascii=False, separators=(",", ":"),
    ))


def test_profile_resolution_uses_symbol_page_and_vocabulary():
    stock = resolve_tool_profile(_request("最近走势怎么样", symbol="600519"))
    assert stock.name == "stock"
    assert "stock_kline" in stock.tools
    assert "stock_activity" not in stock.tools
    assert "finance_news" not in stock.tools
    assert "trading_calendar" not in stock.tools
    assert "first_board_ratings" not in stock.tools

    news = resolve_tool_profile(_request("最新的财经新闻有哪些"))
    assert news.name == "news"
    assert "finance_news" in news.tools
    assert "stock_activity" not in news.tools
    assert "stock_kline" not in news.tools

    limit_up = resolve_tool_profile(_request("2026-10-28 涨停家数是多少"))
    assert limit_up.name.startswith("limit_up")
    assert {"market_summary", "limit_up_events", "trading_calendar"} <= limit_up.tools

    index = resolve_tool_profile(_request("上证指数最近5日走势"))
    assert index.name == "index"
    assert {"market_index_trend", "index_quote"} <= index.tools
    assert "stock_kline" not in index.tools

    sector = resolve_tool_profile(_request("今天哪些板块涨幅居前"))
    assert sector.name.startswith("sector")
    assert {"sector_performance", "hot_stock_ranking", "trading_calendar"} <= sector.tools

    post_limit = resolve_tool_profile(_request("缩量整理策略有哪些"))
    assert post_limit.name == "post_limit"
    assert "post_limit_screen" in post_limit.tools

    review = resolve_tool_profile(_request("复盘 Top10 表现"))
    assert review.name == "review"
    assert "review_high_score_picks" in review.tools

    general = resolve_tool_profile(_request("帮我看看"))
    assert general.name == "general"
    assert {"market_summary", "finance_news", "stock_profile"} <= general.tools

    combined = resolve_tool_profile(_request("600519 最新新闻和财务"))
    assert combined.name.startswith("combined:")
    assert {"stock_financials", "finance_news"} <= combined.tools

    first_board = resolve_tool_profile(_request("首板候选有哪些"))
    assert "first_board_ratings" in first_board.tools


def test_profile_schema_budget_is_materially_smaller_than_full():
    full = _definitions_chars()
    budgets = {
        "stock": 0.40,
        "market": 0.40,
        "index": 0.25,
        "limit_up": 0.35,
        "sector": 0.25,
        "news": 0.25,
        "review": 0.25,
        "post_limit": 0.40,
        "general": 0.60,
    }
    for name, ratio in budgets.items():
        chars = _definitions_chars(PROFILE_TOOLS[name])
        assert chars < full * ratio, f"{name}: {chars} >= {full} * {ratio}"


def test_runner_exposes_only_the_selected_profile():
    runner = AgentRunner(
        _request("600519 最近走势", symbol="600519"),
        _registry(),
        object(),
    )
    names = {item["function"]["name"] for item in runner.definitions}
    assert runner.tool_profile_name == "stock"
    assert {"stock_kline", "stock_profile", "read_tool_result"} <= names
    assert "stock_activity" not in names
    assert "finance_news" not in names
    assert "post_limit_screen" not in names
    assert runner.definition_chars < _definitions_chars()
