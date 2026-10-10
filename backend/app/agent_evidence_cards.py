"""Presentation of current and legacy Agent tool evidence for saved chat responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import AgentEvidenceCard, AgentToolPolicyAudit, AgentToolTrace


def _evidence_title_kind(tool_name: str) -> tuple[str, str]:
    """Map internal tool names to UI evidence categories."""

    mapping: dict[str, tuple[str, str]] = {
        "agent_plan": ("问题理解与工具计划", "execution"),
        "llm_tool_planner": ("LLM 工具规划", "execution"),
        "market_summary": ("市场环境事实", "market"),
        "sector_performance": ("行业板块行情", "market"),
        "sector_stock_ranking": ("板块成分股走势", "market"),
        "hot_stock_ranking": ("同花顺热股榜", "market"),
        "dragon_tiger_list": ("同花顺龙虎榜", "market"),
        "remote_limit_up_pool": ("同花顺涨停池", "limit_up_events"),
        "finance_news": ("财经快讯聚合", "tool"),
        "stock_news": ("个股资讯", "tool"),
        "stock_activity": ("个股近期动态", "tool"),
        "web_search": ("公开网络检索", "tool"),
        "first_board_ratings": ("首板候选池与评分", "candidate_pool"),
        "market_event_pool": ("市场事件查询", "limit_up_events"),
        "limit_up_events": ("涨停事件查询", "limit_up_events"),
        "post_limit_screen": ("涨停后形态筛选", "candidate_pool"),
        "post_limit_path": ("涨停后逐日走势", "tool"),
        "post_limit_statistics": ("涨停后历史统计", "evaluation"),
        "first_board_filter": ("首板条件筛选", "candidate_pool"),
        "first_board_critic": ("评分反证与风险", "critic"),
        "rating_backtest": ("评分历史回测", "evaluation"),
        "rating_evaluation": ("Agent 自我评价", "evaluation"),
        "scoring_policy_status": ("评分策略迭代", "evaluation"),
        "limit_up_event_dates": ("本地数据日期", "data_availability"),
    }
    return mapping.get(tool_name, (tool_name, "tool"))


def _evidence_metrics(output: dict[str, Any]) -> dict[str, Any]:
    """Extract displayable numeric/string metrics from trace output."""

    allowed = (
        "trade_date",
        "data_as_of",
        "captured_at",
        "fetched_at",
        "window_hours",
        "period",
        "source",
        "sector_name",
        "sector_count",
        "member_count",
        "analyzed_count",
        "missing_count",
        "truncated_count",
        "requested_days",
        "requested_limit",
        "rank",
        "change_pct",
        "up_count",
        "down_count",
        "candidate_count",
        "matched_count",
        "pool_count",
        "evaluable_count",
        "coverage_ratio",
        "complete_sample_count",
        "complete_signal_date_count",
        "sample_quality",
        "rule_version",
        "upstream_total",
        "stock_count",
        "event_count",
        "first_board_count",
        "continued_board_count",
        "failed_count",
        "limit_up_count",
        "recall_count",
        "case_count",
        "outcome_ready_count",
        "universe_count",
        "score",
        "rating",
        "confidence",
        "verdict",
        "status",
        "champion_version",
        "challenger_count",
        "latest_challenger",
        "promotion_eligible",
        "activated",
    )
    metrics: dict[str, Any] = {}
    for key in allowed:
        value = output.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            metrics[key] = value
    return {key: value for key, value in metrics.items() if value is not None}


def _flatten_evidence_value(value: Any) -> list[str]:
    """Flatten nested trace output into short display strings."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value[:5]:
            flattened.extend(_flatten_evidence_value(item))
        return flattened
    if isinstance(value, dict):
        name = value.get("name") or value.get("symbol") or value.get("title")
        symbol = value.get("symbol")
        score = value.get("score")
        rating = value.get("rating")
        trade_date_value = value.get("trade_date")
        parts = []
        if name:
            parts.append(str(name))
        if symbol and symbol != name:
            parts.append(str(symbol))
        if trade_date_value:
            parts.append(str(trade_date_value))
        if rating:
            parts.append(f"评级 {rating}")
        if isinstance(score, (int, float)):
            parts.append(f"评分 {score:.1f}")
        if parts:
            return [" · ".join(parts)]
    return []


def _compact_texts(values: list[Any], limit: int = 4) -> list[str]:
    """Deduplicate and trim text fragments for card facts."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:160])
        if len(result) >= limit:
            break
    return result


def build_agent_tool_policy_audit(
    *,
    tool_calls: list[str],
    tool_results: list[AgentToolTrace],
    warnings: list[str] | None = None,
) -> AgentToolPolicyAudit:
    """Compare the LLM planner's requested tools with final backend execution."""

    from app.models import AGENT_CONTROL_TRACE_NAMES, AgentToolPolicyAudit

    planner_calls = _extract_planner_tool_calls(tool_results)
    planner_trace_present = any(
        trace.name == "llm_tool_planner" for trace in tool_results
    )
    final_calls = [tool for tool in tool_calls if tool not in AGENT_CONTROL_TRACE_NAMES]
    if not final_calls:
        final_calls = [
            trace.name
            for trace in tool_results
            if trace.name not in AGENT_CONTROL_TRACE_NAMES
        ]
    graph_plan_present = any(trace.name == "complex_graph_plan" for trace in tool_results)
    graph_compiled = list(final_calls) if graph_plan_present else []
    policy_repaired = [
        trace.name for trace in tool_results
        if isinstance(trace.output.get("policy_repair"), dict)
    ]
    backend_repaired = [
        tool
        for tool in final_calls
        if planner_trace_present
        and tool not in planner_calls
        and tool not in graph_compiled
        and tool not in policy_repaired
    ]
    warnings = warnings or []
    return AgentToolPolicyAudit(
        planner_tool_calls=planner_calls,
        final_tool_calls=final_calls,
        graph_compiled_tools=graph_compiled,
        backend_repaired_tools=backend_repaired,
        policy_repaired_tools=list(dict.fromkeys(policy_repaired)),
        repair_reasons=[
            _repair_reason(tool, tool_results)
            for tool in dict.fromkeys([*backend_repaired, *policy_repaired])
        ],
        safety_fallback_used=any(
            "template fallback" in warning.lower()
            or "safety" in warning.lower()
            or "disabled" in warning.lower()
            for warning in warnings
        ),
    )


def build_agent_evidence_cards(
    tool_results: list[AgentToolTrace],
    warnings: list[str] | None = None,
) -> list[AgentEvidenceCard]:
    """Convert raw tool traces into user-facing evidence cards."""

    from app.models import AgentEvidenceCard

    if not tool_results and not warnings:
        return []

    cards: list[AgentEvidenceCard] = []
    if tool_results:
        policy = build_agent_tool_policy_audit(
            tool_calls=[],
            tool_results=tool_results,
            warnings=warnings,
        )
        outcome_statuses = [
            trace.result.status
            for trace in tool_results
            if trace.result is not None
        ]
        success_count = sum(status == "ok" for status in outcome_statuses)
        empty_count = sum(status == "empty" for status in outcome_statuses)
        partial_count = sum(status == "partial" for status in outcome_statuses)
        error_count = sum(status == "error" for status in outcome_statuses)
        control_count = sum(trace.result is None for trace in tool_results)
        skipped_count = sum(1 for trace in tool_results if trace.status == "skipped")
        cards.append(
            AgentEvidenceCard(
                title="Agent 执行摘要",
                kind="execution",
                status=(
                    "error"
                    if error_count
                    else "skipped"
                    if partial_count
                    else "success"
                ),
                summary=(
                    f"本次回答包含 {len(tool_results)} 条执行记录："
                    f"{success_count} 条有数据、{empty_count} 条无结果、"
                    f"{partial_count} 条部分可用、{error_count} 条失败、"
                    f"{control_count} 条控制记录。"
                ),
                facts=_compact_texts([trace.summary for trace in tool_results[:4]]),
                metrics={
                    "tool_count": len(tool_results),
                    "success_count": success_count,
                    "empty_count": empty_count,
                    "partial_count": partial_count,
                    "error_count": error_count,
                    "control_count": control_count,
                    "skipped_count": skipped_count,
                    "repair_count": len(policy.backend_repaired_tools),
                },
                source_tools=[trace.name for trace in tool_results],
            )
        )
        if policy.planner_tool_calls or policy.backend_repaired_tools:
            cards.append(
                AgentEvidenceCard(
                    title="Planner vs Final",
                    kind="execution",
                    status="skipped" if policy.backend_repaired_tools else "success",
                    summary=(
                        "后端对 LLM 工具计划进行了补救。"
                        if policy.backend_repaired_tools
                        else "LLM 工具计划与最终执行一致。"
                    ),
                    facts=[
                        f"Planner: {', '.join(policy.planner_tool_calls) or '无'}",
                        f"Final: {', '.join(policy.final_tool_calls) or '无'}",
                        *policy.repair_reasons,
                    ],
                    metrics={"repair_count": len(policy.backend_repaired_tools)},
                    source_tools=["llm_tool_planner"],
                )
            )

    for trace in tool_results:
        card = _evidence_card_from_trace(trace)
        if card is not None:
            cards.append(card)

    if warnings:
        cards.append(
            AgentEvidenceCard(
                title="数据与回答限制",
                kind="data_availability",
                status="skipped",
                summary="本次回答存在需要注意的数据限制。",
                facts=_compact_texts(warnings, limit=5),
                source_tools=[],
            )
        )
    return cards


def _evidence_card_from_trace(trace: AgentToolTrace) -> AgentEvidenceCard | None:
    """Create one evidence card from a trace."""

    from app.models import AgentEvidenceCard

    if trace.name.startswith("task_"):
        return None
    output = trace.output or {}
    metrics = _evidence_metrics(output)
    facts = _evidence_facts(trace, output)
    title, kind = _evidence_title_kind(trace.name)

    if trace.name in {
        "llm_tool_answer",
        "template_general_answer",
        "query_understanding",
        "routing_decision",
        "complex_graph_plan",
        "complex_graph_step",
    }:
        return None

    result_status = trace.result.status if trace.result is not None else None
    card_status = trace.status
    if result_status is not None:
        card_status = (
            "success"
            if result_status == "ok"
            else "error"
            if result_status == "error"
            else "skipped"
        )
    status_summary = {
        "empty": "查询成功，但没有匹配数据。",
        "partial": "部分数据源不可用，以下结果不完整。",
    }.get(result_status)
    return AgentEvidenceCard(
        title=title,
        kind=kind,
        status=card_status,
        summary=trace.error or status_summary or trace.summary,
        facts=facts,
        metrics=metrics,
        source_tools=[trace.name],
    )


def _extract_planner_tool_calls(tool_results: list[AgentToolTrace]) -> list[str]:
    """Extract tool names from the LLM planner trace."""

    for trace in tool_results:
        if trace.name != "llm_tool_planner":
            continue
        raw_calls = trace.input.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            return []
        names: list[str] = []
        for raw_call in raw_calls:
            if isinstance(raw_call, dict) and raw_call.get("name"):
                names.append(str(raw_call["name"]))
        return names
    return []


def _repair_reason(
    tool_name: str,
    tool_results: list[AgentToolTrace],
) -> str:
    """Explain why the backend inserted a missing tool."""

    for trace in reversed(tool_results):
        if trace.name != tool_name:
            continue
        policy = trace.output.get("policy_repair")
        if isinstance(policy, dict) and policy.get("reason"):
            return str(policy["reason"])

    reasons = {
        "first_board_ratings": "用户问题需要评分或候选池事实，planner 未覆盖，后端补充 first_board_ratings。",
        "first_board_critic": "用户要求反证、风险或可靠性检查，后端补充 first_board_critic。",
        "limit_up_event_dates": "用户询问本地是否有某日数据，后端补充 limit_up_event_dates。",
        "market_event_pool": "用户询问涨停、跌停或炸板明细，后端补充对应市场事件。",
        "limit_up_events": "用户询问当天涨停/连板/炸板明细，后端补充 limit_up_events。",
        "post_limit_screen": "用户询问涨停后形态名单，后端补充事件锚定的量价筛选。",
        "post_limit_path": "用户询问单票涨停后走势，后端补充锚点后的逐日路径。",
        "post_limit_statistics": "用户询问涨停后历史表现，后端补充成熟样本统计。",
        "sector_performance": "用户询问整个行业板块表现，后端补充 sector_performance。",
        "sector_stock_ranking": "用户询问板块内哪些股票近期走势更强，后端补充 sector_stock_ranking。",
        "finance_news": "用户询问最新财经快讯，后端补充 finance_news。",
        "stock_news": "用户询问指定股票的近期资讯，后端补充 stock_news。",
        "stock_activity": "用户询问指定股票的近期动态，后端补充 stock_activity。",
        "web_search": "用户询问最新外部信息，后端补充 web_search。",
        "rating_backtest": "用户询问评分效果或回测，后端补充 rating_backtest。",
        "rating_evaluation": "用户询问模型复盘或错判样本，后端补充 rating_evaluation。",
        "scoring_policy_status": "用户询问评分策略或权重迭代，后端补充 scoring_policy_status。",
    }
    return reasons.get(tool_name, f"后端补充 {tool_name} 以满足问题所需事实。")


def _evidence_facts(trace: AgentToolTrace, output: dict[str, Any]) -> list[str]:
    """Extract short factual bullets from common trace output fields."""

    raw: list[Any] = [trace.summary]
    for key in (
        "top_candidates",
        "matches",
        "events",
        "items",
        "cases",
        "support_evidence",
        "counter_evidence",
        "warnings",
        "available_dates",
        "reason",
    ):
        value = output.get(key)
        if value:
            raw.extend(_flatten_evidence_value(value))
    return _compact_texts(raw, limit=5)
