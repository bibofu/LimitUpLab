"""Central policy engine for grounding Agent answers with required tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    FirstBoardRating,
    FirstBoardRatingsResponse,
)


class ToolExecution(TypedDict):
    """Mutable result accumulated while executing an LLM tool plan."""

    facts: dict[str, Any]
    tool_results: list[AgentToolTrace]
    tool_call_names: list[str]
    references: list[str]


@dataclass(frozen=True)
class QuestionSignals:
    """Small set of deterministic signals used only as Agent guardrails."""

    requested_date: date | None
    first_board_facts: bool
    rating_explanation: bool
    similar_cases: bool
    stock_kline: bool
    rating_backtest: bool
    critic: bool
    evaluation: bool
    review: bool
    scoring_policy: bool

    @classmethod
    def from_message(cls, message: str) -> "QuestionSignals":
        """Parse guardrail signals once instead of across many repair functions."""

        rating_backtest = looks_like_rating_backtest_question(message)
        scoring_policy = looks_like_scoring_policy_question(message)
        review = looks_like_review_question(message) and not scoring_policy
        evaluation = (
            looks_like_evaluation_question(message)
            and not review
            and not scoring_policy
        )
        return cls(
            requested_date=extract_trade_date(message),
            first_board_facts=looks_like_first_board_facts_question(message),
            rating_explanation=looks_like_rating_explain_question(message),
            similar_cases=looks_like_similar_question(message),
            stock_kline=looks_like_stock_kline_question(message),
            rating_backtest=rating_backtest,
            critic=looks_like_critic_question(message),
            evaluation=evaluation,
            review=review,
            scoring_policy=scoring_policy,
        )

    @property
    def needs_domain_facts(self) -> bool:
        """Return whether a direct LLM answer would need local evidence."""

        return any(
            (
                self.first_board_facts,
                self.rating_explanation,
                self.similar_cases,
                self.stock_kline,
                self.rating_backtest,
                self.critic,
                self.evaluation,
                self.review,
                self.scoring_policy,
            )
        )


RepairAction = Callable[[AgentChatRequest, QuestionSignals, ToolExecution, str | None], None]


@dataclass(frozen=True)
class ToolRepairRule:
    """Declarative contract for one required-tool policy."""

    name: str
    tool_name: str
    reason: str
    matches: Callable[[QuestionSignals], bool]
    repair: RepairAction


class AgentToolPolicyEngine:
    """Reconcile an LLM tool plan with minimum domain evidence requirements."""

    def __init__(
        self,
        tools: AgentToolRegistry,
        *,
        compact_ratings: Callable[[FirstBoardRatingsResponse], dict[str, Any]] | None = None,
    ) -> None:
        self.tools = tools
        self.compact_ratings = compact_ratings or _default_compact_ratings

    def requires_grounding(self, request: AgentChatRequest) -> bool:
        """Return whether planner direct-answer mode must be rejected."""

        return QuestionSignals.from_message(request.message).needs_domain_facts

    def resolve_stock_target(
        self,
        request: AgentChatRequest,
        *,
        context_symbol: str | None = None,
    ) -> str | None:
        """Resolve a stock from explicit request data, text, or recent context."""

        candidates = (
            request.symbol,
            _extract_symbol_hint(request.message),
            request.message,
            context_symbol,
        )
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return self.tools.resolve_stock_symbol(candidate)
            except ValueError:
                continue
        return None

    def reconcile(
        self,
        *,
        request: AgentChatRequest,
        execution: ToolExecution,
        context_symbol: str | None = None,
    ) -> list[str]:
        """Apply all matching rules and return names of repaired tools."""

        signals = QuestionSignals.from_message(request.message)
        if not signals.needs_domain_facts:
            return []

        if self._requested_date_is_missing(signals):
            if not _has_tool_outcome(execution, "limit_up_event_dates"):
                self._repair_data_availability(request, signals, execution, context_symbol)
                return ["limit_up_event_dates"]
            return []

        repaired: list[str] = []
        for rule in self._rules():
            if not rule.matches(signals) or _has_tool_outcome(execution, rule.tool_name):
                continue
            try:
                before = len(execution["tool_results"])
                rule.repair(request, signals, execution, context_symbol)
                if len(execution["tool_results"]) == before:
                    continue
                _mark_latest_trace_as_repair(execution, rule)
                repaired.append(rule.tool_name)
            except Exception as error:  # noqa: BLE001
                self._record_error(
                    execution,
                    rule=rule,
                    tool_input={"message": request.message},
                    summary=f"Policy repair for {rule.tool_name} failed.",
                    error=str(error),
                )
                repaired.append(rule.tool_name)
        return repaired

    def _rules(self) -> tuple[ToolRepairRule, ...]:
        """Return ordered rules; earlier tools may provide facts for later rules."""

        return (
            ToolRepairRule(
                name="rating-facts-required",
                tool_name="first_board_ratings",
                reason="The question requires candidate or rating facts.",
                matches=lambda signals: (
                    signals.first_board_facts or signals.rating_explanation
                ),
                repair=self._repair_ratings,
            ),
            ToolRepairRule(
                name="stock-trend-grounding",
                tool_name="stock_kline",
                reason="A single-stock trend answer requires fresh K-line facts.",
                matches=lambda signals: signals.stock_kline,
                repair=self._repair_stock_kline,
            ),
            ToolRepairRule(
                name="similar-case-grounding",
                tool_name="first_board_similar_cases",
                reason="A historical comparison requires retrieved similar cases.",
                matches=lambda signals: signals.similar_cases,
                repair=self._repair_similar_cases,
            ),
            ToolRepairRule(
                name="rating-backtest-grounding",
                tool_name="rating_backtest",
                reason="A rating-quality claim requires historical backtest facts.",
                matches=lambda signals: signals.rating_backtest,
                repair=self._repair_rating_backtest,
            ),
            ToolRepairRule(
                name="critic-grounding",
                tool_name="first_board_critic",
                reason="A reliability challenge requires supporting and opposing evidence.",
                matches=lambda signals: signals.critic,
                repair=self._repair_critic,
            ),
            ToolRepairRule(
                name="evaluation-grounding",
                tool_name="rating_evaluation",
                reason="A prediction review requires persisted outcomes and evaluation facts.",
                matches=lambda signals: signals.evaluation,
                repair=self._repair_evaluation,
            ),
            ToolRepairRule(
                name="scoring-policy-grounding",
                tool_name="scoring_policy_status",
                reason=(
                    "A scoring-strategy or self-improvement claim requires the active "
                    "Champion, Challenger and out-of-sample gate facts."
                ),
                matches=lambda signals: signals.scoring_policy,
                repair=self._repair_scoring_policy,
            ),
            ToolRepairRule(
                name="high-score-review-grounding",
                tool_name="review_high_score_picks",
                reason="A high-score review requires tracked picks and follow-up outcomes.",
                matches=lambda signals: signals.review,
                repair=self._repair_review,
            ),
        )

    def _requested_date_is_missing(self, signals: QuestionSignals) -> bool:
        requested_date = signals.requested_date
        return requested_date is not None and requested_date not in {
            event.trade_date for event in self.tools.events
        }

    def _repair_data_availability(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, context_symbol
        requested_date = signals.requested_date
        if requested_date is None:
            return
        available_dates = sorted(
            {event.trade_date for event in self.tools.events},
            reverse=True,
        )
        output = {
            "requested_trade_date": requested_date.isoformat(),
            "latest_local_trade_date": (
                available_dates[0].isoformat() if available_dates else None
            ),
            "available_trade_dates": [item.isoformat() for item in available_dates[:20]],
        }
        trace = AgentToolTrace(
            name="limit_up_event_dates",
            input={"requested_trade_date": requested_date.isoformat()},
            summary=f"本地没有 {requested_date.isoformat()}，已返回可用交易日列表。",
            output={
                **output,
                "policy_repair": {
                    "rule": "requested-date-availability",
                    "reason": "The requested trade date is absent from local data.",
                },
            },
        )
        execution["facts"]["limit_up_event_dates"] = output
        execution["tool_results"].append(trace)
        execution["tool_call_names"].append("limit_up_event_dates")
        _extend_references(
            execution,
            [f"missing_trade_date={requested_date.isoformat()}"],
        )

    def _repair_ratings(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        trade_date = request.trade_date or signals.requested_date
        result = self.tools.first_board_ratings(trade_date=trade_date)
        ratings: FirstBoardRatingsResponse = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="first_board_ratings",
            fact_value=self.compact_ratings(ratings),
            references=[f"trade_date={ratings.trade_date.isoformat()}"],
        )

    def _repair_stock_kline(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del signals
        target = self.resolve_stock_target(request, context_symbol=context_symbol)
        if target is None:
            raise ValueError("Cannot resolve the requested stock symbol.")
        result = self.tools.stock_kline(
            symbol=target,
            days=extract_kline_days(request.message),
            end_date=request.trade_date,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="stock_kline",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"symbol={response.symbol}",
                f"data_as_of={response.data_as_of.isoformat()}",
            ],
        )

    def _repair_similar_cases(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        target = self._resolve_first_board_target(
            request,
            signals,
            execution,
            context_symbol,
        )
        if target is None:
            raise ValueError("Cannot resolve a stock and first-board date for similar cases.")
        symbol, trade_date = target
        result = self.tools.similar_cases(symbol=symbol, trade_date=trade_date, limit=5)
        self._record_success(
            execution,
            result=result,
            fact_name="first_board_similar_cases",
            fact_value=result.output.model_dump(mode="json"),
            references=[f"symbol={symbol}", f"trade_date={trade_date.isoformat()}"],
        )

    def _repair_rating_backtest(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        start_date, end_date = self._default_date_range()
        result = self.tools.rating_backtest(
            start_date=start_date,
            end_date=end_date,
            failure_limit=8,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="rating_backtest",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
            ],
        )

    def _repair_critic(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        target = self._resolve_first_board_target(
            request,
            signals,
            execution,
            context_symbol,
        )
        if target is None:
            raise ValueError("Cannot resolve a stock and first-board date for critic review.")
        symbol, trade_date = target
        result = self.tools.first_board_critic(
            symbol=symbol,
            trade_date=trade_date,
            similar_limit=5,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="first_board_critic",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"symbol={response.symbol}",
                f"trade_date={response.trade_date.isoformat()}",
                f"critic_verdict={response.verdict}",
            ],
        )

    def _repair_evaluation(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        start_date, end_date = self._default_date_range()
        result = self.tools.rating_evaluation(
            start_date=start_date,
            end_date=end_date,
            limit=30,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="rating_evaluation",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
            ],
        )

    def _repair_review(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        start_date, end_date = self._default_date_range()
        result = self.tools.review_high_score_picks(
            start_date=start_date,
            end_date=end_date,
            min_score=85,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="review_high_score_picks",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
                f"review_sample_size={response.sample_size}",
            ],
        )

    def _repair_scoring_policy(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        result = self.tools.scoring_policy_status()
        payload = result.output
        champion = payload.get("champion") or {}
        latest = payload.get("latest_optimization") or {}
        challenger = latest.get("challenger_policy") or {}
        self._record_success(
            execution,
            result=result,
            fact_name="scoring_policy_status",
            fact_value=payload,
            references=[
                f"scoring_version={champion.get('version')}",
                f"challenger_version={challenger.get('version')}",
            ],
        )

    def _resolve_first_board_target(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> tuple[str, date] | None:
        facts = execution["facts"]
        rating_facts = facts.get("first_board_ratings")
        filter_facts = facts.get("first_board_filter")
        fact_date = None
        if isinstance(rating_facts, dict):
            fact_date = _parse_date(rating_facts.get("trade_date"))
        trade_date = (
            request.trade_date
            or signals.requested_date
            or fact_date
            or max((event.trade_date for event in self.tools.events), default=None)
        )
        if trade_date is None:
            return None

        explicit = self.resolve_stock_target(request, context_symbol=context_symbol)
        if explicit:
            return explicit, trade_date

        candidates: list[dict[str, Any]] = []
        if isinstance(filter_facts, dict):
            candidates.extend(
                item for item in filter_facts.get("matches", []) if isinstance(item, dict)
            )
        if isinstance(rating_facts, dict):
            candidates.extend(
                item
                for item in rating_facts.get("top_candidates", [])
                if isinstance(item, dict)
            )
        if len(candidates) == 1 and candidates[0].get("symbol"):
            return str(candidates[0]["symbol"]), trade_date
        return None

    def _default_date_range(self) -> tuple[date, date]:
        available_dates = sorted({event.trade_date for event in self.tools.events})
        if not available_dates:
            raise ValueError("No local limit-up events available.")
        return available_dates[max(0, len(available_dates) - 20)], available_dates[-1]

    @staticmethod
    def _record_success(
        execution: ToolExecution,
        *,
        result: ToolResult,
        fact_name: str,
        fact_value: Any,
        references: list[str],
        prepend: bool = False,
    ) -> None:
        execution["facts"][fact_name] = fact_value
        trace = result.trace()
        if prepend:
            execution["tool_results"].insert(0, trace)
            execution["tool_call_names"].insert(0, result.name)
        else:
            execution["tool_results"].append(trace)
            execution["tool_call_names"].append(result.name)
        _extend_references(execution, references)

    @staticmethod
    def _record_error(
        execution: ToolExecution,
        *,
        rule: ToolRepairRule,
        tool_input: dict[str, Any],
        summary: str,
        error: str,
    ) -> None:
        execution["facts"][f"{rule.tool_name}_error"] = error
        execution["tool_results"].append(
            AgentToolTrace(
                name=rule.tool_name,
                input=tool_input,
                summary=summary,
                status="error",
                output={
                    "policy_repair": {
                        "rule": rule.name,
                        "reason": rule.reason,
                    }
                },
                error=error,
            )
        )
        execution["tool_call_names"].append(rule.tool_name)


def extract_trade_date(message: str) -> date | None:
    """Extract a date from common Chinese and numeric expressions."""

    normalized = message.strip()
    full_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", normalized)
    if full_match:
        return _safe_date(*(int(part) for part in full_match.groups()))
    short_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日|号)?", normalized)
    if short_match:
        month, day = (int(part) for part in short_match.groups())
        return _safe_date(date.today().year, month, day)
    return None


def extract_kline_days(message: str) -> int:
    """Extract a bounded trading-day window, defaulting to 20 days."""

    match = re.search(r"(?:最近|近)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    if match is None:
        return 20
    return max(5, min(int(match.group(1)), 60))


def looks_like_first_board_facts_question(message: str) -> bool:
    """Return whether the question explicitly needs first-board candidate facts."""

    return any(term in message for term in ("首板", "候选", "评分靠前", "候选池"))


def looks_like_stock_kline_question(message: str) -> bool:
    """Return whether a question asks about one stock's price trend."""

    lowered = message.lower()
    asks_trend = any(
        term in lowered
        for term in (
            "k线",
            "k-line",
            "走势",
            "趋势",
            "均线",
            "量能",
            "成交量",
            "最近涨跌",
            "近期涨跌",
        )
    )
    return asks_trend and not ("高分" in message and "后续走势" in message)


def looks_like_similar_question(message: str) -> bool:
    """Return whether a question asks for historical similar cases."""

    return any(term in message.lower() for term in ("相似", "历史", "案例", "similar"))


def looks_like_rating_backtest_question(message: str) -> bool:
    """Return whether a question asks for aggregate rating backtesting."""

    return any(
        term in message
        for term in (
            "回测",
            "准不准",
            "准吗",
            "自我评价",
            "评分效果",
            "评级表现",
            "表现怎么样",
            "失败样本",
        )
    )


def looks_like_evaluation_question(message: str) -> bool:
    """Return whether a question asks for persisted prediction evaluation."""

    return any(
        term in message.lower()
        for term in (
            "复盘",
            "预测",
            "表现怎么样",
            "评分最高的票表现",
            "昨天评分最高",
            "哪些评分错了",
            "误判",
            "漏判",
            "因子有效",
            "因子失效",
            "自我改进",
            "evaluation",
        )
    )


def looks_like_review_question(message: str) -> bool:
    """Return whether a question asks to review recent high-score picks."""

    return any(
        term in message
        for term in (
            "高分票",
            "高评分",
            "首板审美",
            "审美",
            "最近评分",
            "后续走势",
            "走得怎么样",
            "规则改进",
            "权重",
            "偏差",
            "复盘高分",
            "复盘一下高分",
        )
    )


def looks_like_scoring_policy_question(message: str) -> bool:
    """Return whether a question asks how rating weights evolve or are activated."""

    lowered = message.lower()
    return any(
        term in lowered
        for term in (
            "评分策略",
            "评分权重",
            "权重优化",
            "权重更新",
            "策略迭代",
            "策略版本",
            "自动学习",
            "自主学习",
            "自动调参",
            "champion",
            "challenger",
        )
    )


def looks_like_critic_question(message: str) -> bool:
    """Return whether a question asks for rating criticism or reliability."""

    return any(
        term in message.lower()
        for term in (
            "靠谱",
            "可信",
            "可靠",
            "质疑",
            "反驳",
            "打脸",
            "高估",
            "低估",
            "过度乐观",
            "critic",
            "critique",
        )
    )


def looks_like_rating_explain_question(message: str) -> bool:
    """Return whether a question needs original candidate rating facts."""

    if (
        looks_like_rating_backtest_question(message)
        or looks_like_evaluation_question(message)
        or looks_like_review_question(message)
        or looks_like_scoring_policy_question(message)
    ):
        return False
    return any(term in message.lower() for term in ("为什么", "评分", "评级", "高分", "低分", "score"))


def _has_tool_outcome(execution: ToolExecution, tool_name: str) -> bool:
    facts = execution["facts"]
    return tool_name in facts or f"{tool_name}_error" in facts


def _extend_references(execution: ToolExecution, references: list[str]) -> None:
    execution["references"] = list(
        dict.fromkeys([*execution["references"], *references])
    )


def _mark_latest_trace_as_repair(
    execution: ToolExecution,
    rule: ToolRepairRule,
) -> None:
    trace = execution["tool_results"][-1]
    trace.output = {
        **trace.output,
        "policy_repair": {
            "rule": rule.name,
            "reason": rule.reason,
        },
    }


def _extract_symbol_hint(message: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _default_compact_ratings(ratings: FirstBoardRatingsResponse) -> dict[str, Any]:
    """Provide a compact fallback serializer for standalone policy tests."""

    return {
        "trade_date": ratings.trade_date.isoformat(),
        "candidate_count": len(ratings.candidates),
        "filtered_out_count": len(ratings.filtered_out),
        "top_candidates": [_compact_rating(item) for item in ratings.candidates[:10]],
    }


def _compact_rating(rating: FirstBoardRating) -> dict[str, Any]:
    return {
        "symbol": rating.facts.symbol,
        "name": rating.facts.name,
        "industry": rating.facts.industry,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
    }
