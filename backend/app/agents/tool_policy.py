"""Central policy engine for grounding Agent answers with required tools."""

from __future__ import annotations

import re
from app.agents.tool_schema import validate_schema_value as _validate_schema_value
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from app.agents.limit_up_execution import execute_limit_up_query
from app.agents.query_contract import (
    build_market_event_query_contract,
    build_limit_up_query_contract,
    extract_board_filters as contract_board_filters,
    extract_market_event_type,
    extract_result_limit,
    extract_market_segment as contract_market_segment,
    extract_trade_date as contract_trade_date,
    looks_like_market_event_query,
)
from app.post_limit_query_contract import (
    build_post_limit_query_contract,
    looks_like_post_limit_path_question,
    looks_like_post_limit_question,
    looks_like_post_limit_statistics_question,
)
from app.agents.tools import (
    AgentToolRegistry,
    ToolResult,
    compact_first_board_position_groups,
    compact_prediction_quality_audit,
)
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    FirstBoardRating,
    FirstBoardRatingsResponse,
)






RepairAction = Callable[[AgentChatRequest, QuestionSignals, ToolExecution, str | None], None]






class AgentToolPolicyEngine:
    """Reconcile an LLM tool plan with minimum domain evidence requirements."""










    # Add missing daily board promotion evidence to the execution result when the policy requires


    # Add missing sector stock ranking evidence to the execution result when the policy requires













    # Add missing post limit statistics evidence to the execution result when the policy requires







    # Resolve the stock and its first-board evidence for policy repair.
    # A None result represents the unavailable or inapplicable branch; callers must check it


    # Append a successful policy repair's facts, trace and references to the current execution.
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

    # Record a failed policy repair as a tool error instead of inventing evidence.
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






def extract_stock_news_days(message: str) -> int:
    """Extract a bounded calendar-day news window, defaulting to seven days."""

    compact = re.sub(r"\s+", "", message)
    if any(term in compact for term in ("近一周", "最近一周", "过去一周", "本周")):
        return 7
    if any(term in compact for term in ("近一个月", "最近一个月", "过去一个月", "近一月")):
        return 30
    match = re.search(r"(?:最近|近|过去)?(\d{1,2})(?:个)?(?:天|日)", compact)
    if match:
        return max(1, min(int(match.group(1)), 30))
    return 7


def extract_market_index_days(message: str) -> int:
    """Extract a bounded broad-index window in trading days."""

    compact = re.sub(r"\s+", "", message)
    if any(term in compact for term in ("近一周", "最近一周", "过去一周", "本周")):
        return 5
    if any(term in compact for term in ("近两周", "最近两周", "过去两周")):
        return 10
    if any(term in compact for term in ("近一个月", "最近一个月", "过去一个月", "近一月")):
        return 20
    match = re.search(r"(?:最近|近|过去)?(\d{1,2})(?:个)?(?:交易日|天|日)", compact)
    if match:
        return max(2, min(int(match.group(1)), 20))
    return 5


def looks_like_market_index_trend_question(message: str) -> bool:
    """Return whether a question asks for broad-index multi-day performance."""

    compact = re.sub(r"\s+", "", message).lower()
    index_terms = (
        "大盘",
        "指数",
        "沪指",
        "上证",
        "深证成指",
        "创业板指",
        "a股走势",
        "a股大盘",
    )
    trend_terms = (
        "走势",
        "趋势",
        "表现",
        "涨跌",
        "行情",
        "近一周",
        "最近一周",
        "本周",
        "近一个月",
    )
    return any(term in compact for term in index_terms) and any(
        term in compact for term in trend_terms
    )


def looks_like_first_board_facts_question(message: str) -> bool:
    """Return whether the question needs the rated candidate pool, not a raw list."""

    return looks_like_first_board_position_question(message) or any(
        term in message
        for term in (
            "首板评分",
            "首板评级",
            "评分靠前",
            "评分最高",
            "高分候选",
            "候选池",
            "候选评分",
        )
    )


def looks_like_first_board_position_question(message: str) -> bool:
    """Return whether position means a pre-board K-line regime classification."""

    if "首板" not in message or "位置" not in message:
        return False
    return not any(
        term in message
        for term in ("首封时间", "封板时间", "几点封板", "几点涨停")
    )


def looks_like_limit_up_event_question(message: str) -> bool:
    """Return whether the question needs raw limit-up events rather than ratings."""

    if looks_like_daily_board_promotion_question(message):
        return False
    if looks_like_first_board_position_question(message):
        return False
    if any(
        term in message
        for term in (
            "评分",
            "评级",
            "候选",
            "回测",
            "复盘",
            "相似",
            "为什么",
            "权重",
            "策略",
        )
    ):
        return False
    return any(
        term in message
        for term in ("涨停", "首板", "连板", "二板", "三板", "炸板", "最高板")
    )


def looks_like_daily_board_promotion_question(message: str) -> bool:
    """Return whether the question asks for empirical board-promotion rates."""

    promotion_terms = ("晋级率", "晋级概率", "晋级情况", "晋级成功率")
    board_terms = ("涨停", "首板", "连板", "二板", "三板", "接力")
    scoring_policy_terms = ("Champion", "Challenger", "冠军", "挑战者", "评分策略")
    if any(term.lower() in message.lower() for term in scoring_policy_terms):
        return False
    stock_detail_question = any(
        term in message for term in ("晋级", "一进二", "1进2", "1-2")
    ) and any(
        term in message
        for term in ("哪些票", "哪些股票", "哪些个股", "股票有哪些", "票有哪些")
    )
    promotion_opening_question = "晋级" in message and any(
        term in message for term in ("开盘", "高开", "平开", "低开")
    )
    return promotion_opening_question or (
        any(term in message for term in promotion_terms)
        and any(term in message for term in board_terms)
    ) or stock_detail_question


def extract_promotion_days(message: str, default_days: int = 5) -> int:
    """Extract a bounded number of recent promotion observations."""

    match = re.search(r"(?:最近|近|过去)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    if match:
        return max(1, min(int(match.group(1)), 60))
    chinese_match = re.search(
        r"(?:最近|近|过去)?\s*([一二三四五六七八九十两]{1,3})\s*(?:个?交易日|天|日)",
        message,
    )
    if chinese_match:
        chinese_days = _parse_chinese_integer(chinese_match.group(1))
        if chinese_days is not None:
            return max(1, min(chinese_days, 60))
    return max(1, min(default_days, 60))


def _parse_chinese_integer(value: str) -> int | None:
    """Parse the small Chinese integers used in trading-day windows."""

    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if "十" not in value:
        return None
    tens, ones = value.split("十", 1)
    tens_value = digits.get(tens, 1) if tens else 1
    ones_value = digits.get(ones, 0) if ones else 0
    return tens_value * 10 + ones_value


def looks_like_promotion_opening_question(message: str) -> bool:
    """Return whether promotion-day opening gaps are the requested evidence."""

    return "晋级" in message and any(
        term in message for term in ("开盘", "高开", "平开", "低开")
    )


def extract_board_filters(message: str) -> tuple[int | None, int | None]:
    """Extract exact or minimum board-height filters for policy repair."""

    return contract_board_filters(message)


def extract_market_segment(message: str) -> str | None:
    """Extract an explicit A-share board segment from the user question."""

    return contract_market_segment(message)


def looks_like_broad_sector_ranking_question(message: str) -> bool:
    """Return whether the user asks for a whole-market sector ranking."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message)
    if not any(term in compact for term in ("板块", "行业", "概念")):
        return False
    if any(
        term in compact for term in ("股票", "个股", "成分股", "哪些票", "什么票")
    ):
        return False
    if not any(
        term in compact
        for term in (
            "表现",
            "走势",
            "行情",
            "涨跌",
            "强弱",
            "领涨",
            "领跌",
            "涨得",
            "跌得",
            "上涨",
            "下跌",
            "排名",
            "排行",
        )
    ):
        return False

    category_match = re.search(r"板块|行业|概念", compact)
    if category_match is None:
        return False
    prefix = compact[: category_match.start()]
    leading_terms = (
        "请问",
        "帮我看看",
        "看看",
        "分析一下",
        "分析",
        "总结一下",
        "今天",
        "今日",
        "最近",
        "近期",
        "目前",
        "现在",
        "全市场",
        "市场",
        "大盘",
        "A股",
        "a股",
    )
    changed = True
    while prefix and changed:
        changed = False
        for term in leading_terms:
            if prefix.startswith(term):
                prefix = prefix[len(term) :]
                changed = True
                break
    return not prefix or any(
        term in prefix for term in ("哪些", "什么", "哪个", "哪几个", "有哪", "谁")
    )


def extract_sector_query(message: str) -> str | None:
    """Extract a named industry from common Chinese sector questions."""

    if looks_like_broad_sector_ranking_question(message):
        return None

    compact = re.sub(r"\s+", "", message)
    patterns = (
        r"(?:今天|今日|最近|近期)?(.{1,16}?)(?:板块|行业|概念)(?:今天|今日)?(?:表现|走势|行情|涨跌|强弱|资金|怎么样|如何|为何|为什么)",
        r"(?:今天|今日|最近|近期)?(.{1,16}?)(?:板块|行业|概念)",
    )
    generic = {
        "哪些",
        "什么",
        "哪个",
        "行业",
        "板块",
        "概念",
        "热门",
        "强势",
        "弱势",
        "A股",
        "a股",
    }
    for pattern in patterns:
        matched = re.search(pattern, compact)
        if not matched:
            continue
        candidate = matched.group(1)
        candidate = re.sub(r"^(?:请问|看看|分析一下|分析|总结一下)", "", candidate)
        if (
            candidate
            and candidate not in generic
            and not any(
                term in candidate
                for term in ("哪些", "什么", "哪个", "哪几个", "有哪", "谁")
            )
            and len(candidate) <= 12
        ):
            return candidate
    return None


def looks_like_market_environment_question(message: str) -> bool:
    """Return whether a question requests a broad current market review."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    explicit_terms = (
        "市场环境",
        "市场情况",
        "盘面环境",
        "盘面情况",
        "市场全貌",
        "市场概况",
        "市场综述",
    )
    if any(term in compact for term in explicit_terms):
        return True
    return any(
        phrase in compact
        for phrase in (
            "今天市场怎么样",
            "今日市场怎么样",
            "今天a股怎么样",
            "今日a股怎么样",
            "现在市场怎么样",
        )
    )


def looks_like_sector_performance_question(message: str) -> bool:
    """Return whether text asks about whole-sector market performance."""

    if not any(term in message for term in ("板块", "行业", "概念")):
        return False
    asks_performance = any(
        term in message
        for term in (
            "表现",
            "走势",
            "行情",
            "涨跌",
            "强弱",
            "资金流",
            "净流入",
            "成交额",
            "领涨",
            "领跌",
            "涨得",
            "跌得",
            "上涨",
            "下跌",
        )
    )
    if not asks_performance:
        return False
    if any(term in message for term in ("首板", "涨停", "评分", "高分票")):
        return "板块表现" in message or "行业表现" in message
    return True


def looks_like_sector_stock_ranking_question(message: str) -> bool:
    """Return whether text asks to compare stocks inside one named sector."""

    compact = re.sub(r"\s+", "", message)
    if not any(term in compact for term in ("板块", "行业", "概念")):
        return False
    asks_stocks = any(
        term in compact for term in ("股票", "个股", "成分股", "哪些票", "什么票")
    )
    asks_comparison = any(
        term in compact
        for term in (
            "走势好",
            "表现好",
            "强势",
            "趋势",
            "排名",
            "排行",
            "涨得好",
            "涨得多",
        )
    )
    return asks_stocks and asks_comparison


def extract_sector_trend_days(message: str) -> int:
    """Extract a bounded daily trend window from a sector-stock question."""

    compact = re.sub(r"\s+", "", message)
    matched = re.search(r"(?:近|最近)?(\d{1,2})(?:个)?(?:交易)?日", compact)
    if matched:
        return max(5, min(int(matched.group(1)), 60))
    if any(term in compact for term in ("一个月", "1个月")):
        return 20
    if any(term in compact for term in ("两个月", "2个月")):
        return 40
    return 20


def looks_like_hot_stock_question(message: str) -> bool:
    """Return whether the user asks for a current stock-popularity ranking."""

    compact = re.sub(r"\s+", "", message).lower()
    explicit_ranking = any(
        term in compact
        for term in (
            "热股榜",
            "热门股票",
            "热门股",
            "人气榜",
            "人气排名",
            "热度排名",
            "哪些股票热门",
        )
    )
    stock_terms = ("股票", "个股", "哪些票", "什么票", "票比较")
    popularity_terms = ("热门", "人气高", "热度高", "关注度高")
    return explicit_ranking or (
        any(term in compact for term in stock_terms)
        and any(term in compact for term in popularity_terms)
    )


def looks_like_web_search_question(message: str) -> bool:
    """Return whether an answer needs current public-web evidence."""

    if any(
        term in message
        for term in (
            "新闻",
            "消息",
            "资讯",
            "公告",
            "政策",
            "研报",
            "舆情",
            "催化",
            "异动原因",
            "上涨原因",
            "下跌原因",
            "大涨原因",
            "大跌原因",
        )
    ):
        return True
    return "为什么" in message and any(
        term in message for term in ("上涨", "下跌", "大涨", "大跌", "异动")
    )


def looks_like_finance_news_question(message: str) -> bool:
    """Return whether the user asks for a broad current financial-news digest."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    news_terms = ("新闻", "快讯", "资讯", "消息", "消息面")
    if "财经" in compact and any(term in compact for term in news_terms):
        return True
    asks_news = any(term in compact for term in news_terms)
    broad_scope = any(
        term in compact
        for term in (
            "最新",
            "今天",
            "今日",
            "近期",
            "最近",
            "这两天",
            "刚刚",
            "刚发生",
            "有什么",
            "汇总",
            "总结",
            "整理",
        )
    ) or compact in news_terms
    specific_scope = any(
        term in compact
        for term in (
            "个股",
            "公司",
            "板块",
            "行业",
            "公告",
            "研报",
            "关于",
            "为什么",
            "原因",
        )
    ) or _has_specific_news_subject(compact, news_terms)
    return asks_news and broad_scope and not specific_scope


def looks_like_stock_news_question(message: str) -> bool:
    """Return whether text asks for news about one named stock or company."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    news_terms = ("新闻", "资讯", "消息", "公告", "研报", "舆情")
    if not any(term in compact for term in news_terms):
        return False
    # Compound plans can establish a stock set in an earlier clause and request
    # news conditionally. Keep that stock-scoped even if an earlier clause also
    # mentioned an industry, or the generic web guard will block the Planner.
    dynamic_stock_scope = any(
        term in compact
        for term in (
            "这些股票",
            "交集股票",
            "候选股票",
            "候选股",
            "首板",
            "龙虎榜",
            "该股",
            "股票",
            "k线",
        )
    )
    if not dynamic_stock_scope and any(
        term in compact for term in ("板块", "行业", "宏观", "政策")
    ):
        return False
    explicit_stock_scope = any(
        term in compact
        for term in ("这只股票", "这只票", "该股", "个股", "这家公司", "该公司")
    ) or re.search(r"(?<!\d)\d{6}(?!\d)", compact) is not None
    return (
        dynamic_stock_scope
        or explicit_stock_scope
        or _has_specific_news_subject(compact, news_terms)
    )


def looks_like_stock_activity_question(message: str) -> bool:
    """Return whether text requests a broad recent update for one stock."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    activity_terms = ("动态", "近况", "发生了什么", "最近怎么样", "近期怎么样")
    if not any(term in compact for term in activity_terms):
        return False
    if any(term in compact for term in ("板块", "行业", "市场", "大盘")):
        return False
    explicit_stock_scope = any(
        term in compact
        for term in ("这只股票", "这只票", "该股", "个股", "这家公司", "该公司")
    ) or re.search(r"(?<!\d)\d{6}(?!\d)", compact) is not None
    return explicit_stock_scope or _has_specific_news_subject(compact, activity_terms)


def _has_specific_news_subject(
    compact: str,
    news_terms: tuple[str, ...],
) -> bool:
    """Detect an entity prefix without maintaining a brittle stock-name list."""

    generic_prefix_terms = (
        "请",
        "帮我",
        "给我",
        "麻烦",
        "看一下",
        "看看",
        "整理一下",
        "整理",
        "汇总",
        "总结",
        "概括",
        "补一下",
        "今天",
        "今日",
        "最近",
        "近期",
        "这两天",
        "刚刚",
        "刚发生",
        "最新",
        "的",
        "财经圈",
        "财经",
        "金融市场",
        "资本市场",
        "a股市场",
        "a股",
        "市场",
        "宏观",
    )

    # Check whether the wording contains an entity marker required by this question classifier.
    def has_entity(prefix: str) -> bool:
        residue = prefix
        for term in sorted(generic_prefix_terms, key=len, reverse=True):
            residue = residue.replace(term, "")
        return bool(residue)

    for marker in ("有什么", "有哪些", "出了哪些", "发生了什么"):
        if marker in compact and has_entity(compact.split(marker, 1)[0]):
            return True
    for news_term in news_terms:
        plain_entity_suffix = f"的{news_term}"
        if compact.endswith(plain_entity_suffix) and has_entity(
            compact[: -len(plain_entity_suffix)]
        ):
            return True
        for modifier in ("最新", "最近", "近期", "今天", "今日"):
            suffix = f"{modifier}的{news_term}"
            plain_suffix = f"{modifier}{news_term}"
            if compact.endswith(suffix) and has_entity(compact[: -len(suffix)]):
                return True
            if compact.endswith(plain_suffix) and has_entity(compact[: -len(plain_suffix)]):
                return True
    return False


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


def looks_like_rating_backtest_question(message: str) -> bool:
    """Return whether a question asks for aggregate rating backtesting."""

    explicit = any(
        term in message
        for term in (
            "回测",
            "准不准",
            "准吗",
            "自我评价",
            "评分效果",
            "评级表现",
            "失败样本",
        )
    )
    return explicit or (
        "表现怎么样" in message
        and any(term in message for term in ("评分", "评级", "预测", "高分票"))
    )


def looks_like_prediction_quality_question(message: str) -> bool:
    """Return whether a claim needs the source-aware prediction audit."""

    lowered = message.lower()
    return any(
        term in lowered
        for term in (
            "预测质量审计",
            "预测质量",
            "结果覆盖率",
            "outcome覆盖",
            "样本完整",
            "样本成熟",
            "基线对比",
            "随机基线",
            "封板基线",
            "v3准备",
            "v3 准备",
            "评分v3",
            "评分 v3",
        )
    )


def looks_like_evaluation_question(message: str) -> bool:
    """Return whether a question asks for persisted prediction evaluation."""

    explicit = any(
        term in message.lower()
        for term in (
            "复盘",
            "预测",
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
    return explicit or (
        "表现怎么样" in message
        and any(term in message for term in ("评分", "高分票", "推荐票"))
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
        or looks_like_prediction_quality_question(message)
        or looks_like_evaluation_question(message)
        or looks_like_review_question(message)
        or looks_like_scoring_policy_question(message)
    ):
        return False
    return any(
        term in message.lower()
        for term in ("评分", "评级", "高分", "低分", "分高", "分低", "score")
    )


# Check whether execution already contains an outcome for a tool, including unsuccessful outcomes.
def _has_tool_outcome(execution: ToolExecution, tool_name: str) -> bool:
    facts = execution["facts"]
    return tool_name in facts or f"{tool_name}_error" in facts


# Merge new evidence references into the execution result without duplicates.
def _extend_references(execution: ToolExecution, references: list[str]) -> None:
    execution["references"] = list(
        dict.fromkeys([*execution["references"], *references])
    )


# Attach the policy rule and repair reason to the newly added tool trace.
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
    if trace.result is not None:
        trace.result.payload = trace.output


# Extract an explicit stock-code hint from the user's message.
def _extract_symbol_hint(message: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


# Parse an optional date argument before applying it to a query.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


# Construct a calendar date while handling invalid year/month/day combinations.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
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
        "position_classification": compact_first_board_position_groups(
            ratings.candidates
        ),
    }


# Keep the rating fields needed as answer evidence rather than forwarding the entire model.
def _compact_rating(rating: FirstBoardRating) -> dict[str, Any]:
    return {
        "symbol": rating.facts.symbol,
        "name": rating.facts.name,
        "industry": rating.facts.industry,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
    }
