"""Grounded stock links and deterministic follow-ups for chat presentation."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import AgentStockMention, AgentToolTrace


def extract_agent_stock_mentions(
    answer: str,
    tool_results: list[AgentToolTrace],
) -> list[AgentStockMention]:
    """Find answer stocks from structured tool outputs without trusting LLM URLs."""

    from app.models import AgentStockMention

    mentions: dict[tuple[str, str], AgentStockMention] = {}

    # Recursively inspect nested tool payloads, carrying the inherited trade date to stock
    # entities.
    def visit(value: Any, inherited_trade_date: date | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, inherited_trade_date)
            return
        if not isinstance(value, dict):
            return

        trade_date = _agent_mention_trade_date(value, inherited_trade_date)
        raw_symbol = str(value.get("symbol") or "").strip()
        symbol = raw_symbol.zfill(6) if raw_symbol.isdigit() else ""
        name = str(value.get("name") or "").strip()
        if (
            len(symbol) == 6
            and 1 < len(name) <= 20
            and name != symbol
            and name in answer
        ):
            key = (symbol, name)
            existing = mentions.get(key)
            if existing is None or (existing.trade_date is None and trade_date is not None):
                mentions[key] = AgentStockMention(
                    name=name,
                    symbol=symbol,
                    trade_date=trade_date,
                )

        for child in value.values():
            visit(child, trade_date)

    for trace in tool_results:
        visit(trace.output)

    # The key compares `len(item.name)` (negated for descending order), then symbol.
    return sorted(
        mentions.values(),
        key=lambda item: (-len(item.name), item.symbol),
    )


def _agent_mention_trade_date(
    value: dict[str, Any],
    fallback: date | None,
) -> date | None:
    """Resolve the nearest business date attached to a stock-shaped tool fact."""

    for key in ("trade_date", "base_trade_date", "detail_trade_date", "data_as_of"):
        raw = value.get(key)
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return fallback


def build_agent_suggested_questions(
    *,
    intent: str,
    stock_mentions: list[AgentStockMention],
) -> list[str]:
    """Build safe, capability-aware follow-ups without asking the LLM to invent them."""

    suggestions: list[str] = []
    if len(stock_mentions) == 1:
        stock = stock_mentions[0]
        subject = f"{stock.name}（{stock.symbol}）"
        if intent not in {"rating_explain", "llm_explanation"}:
            suggestions.append(f"{subject}的评分依据是什么？")
        if intent != "risk_summary":
            suggestions.append(f"{subject}的主要风险和数据缺口是什么？")
        suggestions.append(f"{subject}最近 20 个交易日走势如何？")

    intent_suggestions: dict[str, tuple[str, ...]] = {
        "greeting": (
            "总结最新交易日的首板结构",
            "最新一进二 Top10 的主要风险有哪些？",
            "复盘最近 5 个交易日的一进二晋级率",
        ),
        "capability_intro": (
            "总结最新交易日的首板结构",
            "解释最新一进二 Top10 的评分依据",
            "查看当前数据有哪些缺口",
        ),
        "market_context": (
            "结合市场环境总结最新首板结构",
            "最新交易日哪些板块的首板最集中？",
            "复盘最近 5 个交易日的一进二晋级率",
        ),
        "limit_up_query": (
            "只看最新交易日首次涨停且封住的股票",
            "按板块归纳这批涨停股票",
            "这批股票中有哪些主要风险？",
        ),
        "daily_board_promotion": (
            "拆解最近 5 日首板到二板的样本数",
            "最近 5 日连板晋级率如何变化？",
            "对照一进二 Top10 的实际晋级表现",
        ),
        "prediction_quality_audit": (
            "当前预测样本和 Outcome 完整度如何？",
            "Top10 与全市场基线的差异是什么？",
            "哪些结论仍然因为样本不足不能成立？",
        ),
        "data_availability": (
            "本地最新可用交易日是哪天？",
            "总结本地最新交易日的首板结构",
            "当前数据还有哪些缺口？",
        ),
        "out_of_scope": (
            "你目前能回答哪些收盘后研究问题？",
            "总结本地最新交易日的首板结构",
            "查看当前数据有哪些缺口",
        ),
    }
    default_suggestions = (
        "按板块归纳最新首板候选",
        "这些候选的共同风险和数据缺口有哪些？",
        "复盘最近 5 个交易日的一进二晋级率",
    )
    suggestions.extend(intent_suggestions.get(intent, default_suggestions))

    deduplicated: list[str] = []
    for suggestion in suggestions:
        if suggestion not in deduplicated:
            deduplicated.append(suggestion)
        if len(deduplicated) >= 3:
            break
    return deduplicated
