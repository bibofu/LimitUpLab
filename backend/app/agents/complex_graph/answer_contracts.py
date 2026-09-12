"""Scenario-specific answer contracts for bounded complex graph responses."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.tool_policy import ToolExecution


@dataclass(frozen=True)
class ScenarioAnswerContract:
    fallback_intent: str
    instruction: str
    progress_label: str
    intersection_output: bool = False


_CONTRACTS = {
    "hot_limit_up_rating_intersection_v1": ScenarioAnswerContract(
        fallback_intent="hot_limit_up_rating_intersection",
        instruction=" Answer only for the observed popularity/limit-up intersection and its returned ratings.",
        progress_label="正在基于动态交集和评分事实生成回答",
        intersection_output=True,
    ),
    "top_ratings_then_kline_v2": ScenarioAnswerContract(
        fallback_intent="top_ratings_then_kline",
        instruction=" Identify the returned top-rated candidates first, then compare each candidate only with its corresponding K-line facts.",
        progress_label="正在比较高评分候选及其 K 线证据",
    ),
    "rating_dragon_tiger_branch_v2": ScenarioAnswerContract(
        fallback_intent="rating_dragon_tiger_branch",
        instruction=" Separate candidates with returned Dragon-Tiger evidence from an empty branch; when empty, use only returned K-line and news fallback evidence and disclose the boundary.",
        progress_label="正在整理候选与龙虎榜分支证据",
    ),
    "empty_news_fallback_v2": ScenarioAnswerContract(
        fallback_intent="empty_news_fallback",
        instruction=" If the news observation is empty or error, state that it returned no usable rows and summarize only returned K-line/activity fallback evidence; otherwise report the returned news normally. Never infer that no material news exists.",
        progress_label="正在整理新闻空结果与补充证据",
    ),
    "partial_stock_comparison_v2": ScenarioAnswerContract(
        fallback_intent="partial_stock_comparison",
        instruction=" Preserve every successful stock result, identify failed stock lookups explicitly, and compare only dimensions supported for the successful candidates.",
        progress_label="正在整理多股票比较与失败披露",
    ),
}


def scenario_answer_contract(scenario: str) -> ScenarioAnswerContract:
    try:
        return _CONTRACTS[scenario]
    except KeyError as error:
        raise ValueError(f"missing answer contract for scenario: {scenario}") from error


def apply_scenario_disclosures(scenario: str, answer: str, execution: ToolExecution) -> str:
    """Append deterministic data-boundary disclosures when an observed branch requires one."""

    traces = execution["tool_results"]
    additions: list[str] = []
    if scenario == "empty_news_fallback_v2":
        news = [item for item in traces if item.name == "stock_news"]
        if news and any(item.result and item.result.status in {"empty", "error"} for item in news):
            additions.append(
                "本次新闻检索未返回可用结果；补充判断仅基于已返回的 K 线和个股动态，不能据此断言不存在其他重大新闻。"
            )
    elif scenario == "rating_dragon_tiger_branch_v2":
        dragon = [item for item in traces if item.name == "dragon_tiger_list"]
        if dragon and any(item.result and item.result.status in {"empty", "partial", "error"} for item in dragon):
            additions.append(
                "本次龙虎榜查询未返回完整可用记录；机构行为无法据此判断，补充结论仅使用已返回的 K 线和新闻证据。"
            )
    elif scenario == "partial_stock_comparison_v2":
        failed_symbols = list(
            dict.fromkeys(
                str(item.input.get("symbol"))
                for item in traces
                if item.name == "stock_kline"
                and item.result is not None
                and item.result.status == "error"
                and item.input.get("symbol")
            )
        )
        if failed_symbols:
            additions.append(
                f"数据边界：{'、'.join(failed_symbols)} 的 K 线获取失败；比较仅保留其他成功返回的候选。"
            )
    for addition in additions:
        if addition not in answer:
            answer = f"{answer.rstrip()}\n\n{addition}"
    return answer


def violates_scenario_contract(
    scenario: str, answer: str, execution: ToolExecution
) -> bool:
    """Reject cross-scenario language and answers that omit observed target entities."""

    if scenario == "hot_limit_up_rating_intersection_v1":
        return False
    if any(term in answer for term in ("热股 Top10 与涨停", "热股和涨停股交集", "热股与涨停交集")):
        return True
    traces = execution["tool_results"]
    if scenario in {
        "top_ratings_then_kline_v2",
        "rating_dragon_tiger_branch_v2",
        "partial_stock_comparison_v2",
    }:
        successful_symbols = {
            str(item.input["symbol"])
            for item in traces
            if item.name == "stock_kline"
            and item.result is not None
            and item.result.status in {"ok", "partial"}
            and isinstance(item.input.get("symbol"), str)
        }
        return bool(successful_symbols) and any(
            symbol not in answer for symbol in successful_symbols
        )
    if scenario == "empty_news_fallback_v2":
        targets = {
            str(item.input["symbol"])
            for item in traces
            if item.name in {"stock_news", "stock_kline", "stock_activity"}
            and isinstance(item.input.get("symbol"), str)
        }
        return bool(targets) and not any(target in answer for target in targets)
    return False
