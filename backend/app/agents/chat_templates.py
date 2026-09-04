"""Deterministic, facts-only answer templates for the chat agent."""

import math
import re
from typing import Any

from app.agents.query_contract import (
    extract_result_limit,
    looks_like_exhaustive_request as _looks_like_exhaustive_list_request,
)
from app.agents.tool_policy import (
    QuestionSignals as _QuestionSignals,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
)
from app.models import AgentChatRequest


IDEOGRAPHIC_COMMA = "\u3001"
UNANSWERABLE_TEXT = "抱歉，该问题无法回答"
TEXT = {
    "greeting": "你好，我是 LimitUpLab 的首板 Agent。我可以总结今日首板、解释个股评分、分析风险，也可以查询热门股票、财经快讯、个股新闻、个股走势和市场环境。",
    "capability": "我是 LimitUpLab V1 首板复盘 Agent。我使用最新完整收盘数据、热门题材、新闻财报和个股日 K 线生成低位挖掘观察池，并为已涨停首板生成一进二 Top10，持续复盘 D+1 至 D+5 走势、晋级率和评分表现；也可以查询带来源和时间的热门股票榜单、财经快讯、个股新闻及近期动态。我不提供盘中实时行情、买卖指令、仓位、目标价或收益承诺。",
    "smalltalk": "我在。你可以直接问首板候选、板块分布、评分理由、风险或个股走势。",
    "prompt_injection": "我不能执行改变系统规则、泄露内部提示或调用未授权工具的指令。你可以继续询问 LimitUpLab 支持的收盘复盘问题。",
    "out_of_scope": UNANSWERABLE_TEXT,
    "unsafe": "我不能给出直接交易指令、资金配比、价格预测或回报承诺。我可以基于结构化数据分析评分理由、风险、板块热度和市场环境。",
    "unknown": UNANSWERABLE_TEXT,
    "safety": "\u4ee5\u4e0a\u4e3a\u57fa\u4e8e\u672c\u5730\u7ed3\u6784\u5316\u6570\u636e\u7684\u590d\u76d8\u5206\u6790\uff0c\u4e0d\u6784\u6210\u4e70\u5356\u5efa\u8bae\u3002",
}

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


def _asks_for_bottom_sector_ranking(message: str) -> bool:
    """Return whether a broad sector question explicitly asks for laggards."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message)
    return any(
        term in compact
        for term in (
            "领跌",
            "跌得最",
            "跌幅最大",
            "跌幅靠前",
            "最弱",
            "垫底",
        )
    )


def _template_answer_from_tool_facts(
    *,
    request: AgentChatRequest,
    intent: str,
    facts: dict[str, Any],
) -> str:
    """Build a useful fallback answer from tool facts when final LLM times out."""

    if _QuestionSignals.from_message(request.message).market_environment:
        return _template_market_environment_answer(facts)

    if "market_event_pool" in facts:
        payload = facts["market_event_pool"]
        items = payload.get("items", []) if isinstance(payload, dict) else []
        matched_count = payload.get("matched_count", 0)
        event_label = payload.get("event_label") or "市场事件"
        market_label = payload.get("market_label") or "全市场"
        lines = [
            f"{payload.get('trade_date')} {market_label}{event_label}股票共 "
            f"{matched_count} 只。"
        ]
        if payload.get("result_mode") != "count":
            for index, item in enumerate(items, start=1):
                metrics: list[str] = []
                if item.get("change_pct") is not None:
                    metrics.append(f"涨跌幅 {item['change_pct']:+.2f}%")
                if item.get("industry"):
                    metrics.append(f"行业 {item['industry']}")
                suffix = f"：{'，'.join(metrics)}" if metrics else ""
                lines.append(
                    f"{index}. {item.get('name')}（{item.get('symbol')}）{suffix}"
                )
            if matched_count and not items:
                lines.append("本轮没有返回可展示的股票明细。")
            if len(items) < matched_count:
                lines.append(f"当前先列出前 {len(items)} 只。")
        lines.append("不构成买卖建议。")
        return "\n".join(lines)

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
        matched_sectors = sector.get("matched_sectors") or []
        if matched_sectors:
            requested_sector = sector.get("requested_sector") or sector.get("sector_name")
            lines = [
                f"{sector.get('data_as_of')} “{requested_sector}”对应多个行业，"
                "分别列示，避免把其中一个细分行业当成整个板块："
            ]
            for item in matched_sectors:
                row = (
                    f"- {item.get('sector_name')}：涨跌幅 "
                    f"{float(item.get('change_pct') or 0):+.2f}%，行业排名 "
                    f"{item.get('rank')}/{sector.get('sector_count')}"
                )
                if item.get("up_count") is not None and item.get("down_count") is not None:
                    row += (
                        f"，上涨 {item.get('up_count')} 家、"
                        f"下跌 {item.get('down_count')} 家"
                    )
                lines.append(row + "。")
        elif sector.get("sector_name"):
            sector_label = (
                "概念板块" if sector.get("sector_type") == "concept" else "行业板块"
            )
            change_pct = sector.get("change_pct")
            first_line = f"{sector.get('data_as_of')} {sector.get('sector_name')}{sector_label}"
            first_line += (
                f"涨跌幅 {float(change_pct):+.2f}%"
                if isinstance(change_pct, (int, float))
                else "涨跌幅暂缺"
            )
            if sector.get("rank") is not None and sector.get("sector_count"):
                first_line += (
                    f"，排名 {sector.get('rank')}/{sector.get('sector_count')}"
                )
            lines = [first_line + "。"]
            if sector.get("up_count") is not None and sector.get("down_count") is not None:
                breadth = (
                    f"上涨 {sector.get('up_count')} 家，下跌 "
                    f"{sector.get('down_count')} 家"
                )
                if sector.get("amount_yi") is not None:
                    breadth += f"，成交额 {sector.get('amount_yi')} 亿元"
                if sector.get("net_inflow_yi") is not None:
                    breadth += f"，资金净流入 {sector.get('net_inflow_yi')} 亿元"
                lines.append(breadth + "。")
            if sector.get("leader_name"):
                leader = f"领涨股为 {sector.get('leader_name')}"
                if sector.get("leader_change_pct") is not None:
                    leader += f"，涨跌幅 {sector.get('leader_change_pct')}%"
                lines.append(leader + "。")
            returns = []
            if sector.get("return_5d_pct") is not None:
                returns.append(f"近5日 {float(sector.get('return_5d_pct')):+.2f}%")
            if sector.get("return_20d_pct") is not None:
                returns.append(f"近20日 {float(sector.get('return_20d_pct')):+.2f}%")
            if returns:
                lines.append("已发生的区间走势：" + "，".join(returns) + "。")
            if not sector.get("data_fresh"):
                lines.append("板块历史数据未到请求日期，以上按最近可用交易日展示。")
        else:
            show_bottom = _asks_for_bottom_sector_ranking(request.message)
            ranking_key = "bottom_sectors" if show_bottom else "top_sectors"
            ranking_label = "跌幅靠前" if show_bottom else "涨幅靠前"
            lines = [f"{sector.get('data_as_of')} 行业板块{ranking_label}："]
            lines.extend(
                f"- {item.get('sector_name')}："
                f"{float(item.get('change_pct') or 0):+.2f}%"
                for item in sector.get(ranking_key, [])
            )
        if "web_search" in facts:
            lines.append("相关公开信息：")
            lines.extend(
                f"- {item.get('title')}：{item.get('url')}"
                for item in facts["web_search"].get("results", [])[:3]
            )
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "sector_stock_ranking" in facts:
        ranking = facts["sector_stock_ranking"]
        items = ranking.get("items", [])
        lines = [
            f"截至 {ranking.get('data_as_of')}，{ranking.get('sector_name')}板块"
            f"本轮表现靠前的 {len(items)} 只个股："
        ]
        trend_labels = {
            "rising": "上升",
            "oscillating": "震荡",
            "falling": "回落",
            "insufficient": "数据不足",
        }
        for item in items:
            metrics = [
                f"状态 {trend_labels.get(item.get('trend'), item.get('trend'))}",
            ]
            if item.get("return_5d_pct") is not None:
                metrics.append(f"近5日 {item['return_5d_pct']:+.2f}%")
            if item.get("return_20d_pct") is not None:
                metrics.append(f"近20日 {item['return_20d_pct']:+.2f}%")
            lines.append(
                f"{item.get('rank')}. {item.get('name')}（{item.get('symbol')}）："
                + "，".join(metrics)
                + "。"
            )
        if len(items) < ranking.get("requested_limit", 10):
            lines.append("部分成分股数据不足，本轮未能补足请求数量。")
        lines.append("以上排名只比较已发生的收盘趋势与量能结构，不代表后续上涨概率。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "daily_board_promotion" in facts:
        return _template_daily_board_promotion_answer(facts["daily_board_promotion"])

    if "first_board_discovery" in facts:
        discovery = facts["first_board_discovery"]
        draft = discovery.get("recommendation_draft") or {}
        draft_candidates = draft.get("candidates") or []
        if draft_candidates:
            base_by_symbol = {
                item.get("symbol"): item
                for item in discovery.get("candidates", [])
                if item.get("symbol")
            }
            lines = [
                f"基于 {draft.get('base_date')} 收盘情况，并更新至 "
                f"{str(draft.get('refreshed_at', ''))[:16]} 的低位挖掘观察池如下："
            ]
            for index, item in enumerate(draft_candidates, start=1):
                base = base_by_symbol.get(item.get("symbol"), {})
                themes = base.get("themes") or []
                theme = themes[0] if themes else {}
                latest_news = item.get("latest_news") or []
                financial = item.get("financial_report") or {}
                lines.extend(
                    [
                        f"{index}. {item.get('name')}({item.get('symbol')})，研究分 {item.get('draft_score')}。",
                        f"   1. 题材：{theme.get('name') or item.get('sector') or '暂无明确题材'}，题材涨幅 {theme.get('change_pct', '暂无')}%。",
                        f"   2. 新闻和财报：{latest_news[0].get('title') if latest_news else (base.get('news_catalysts') or ['暂无明确催化'])[0]}；营收同比 {financial.get('operating_income_yoy_pct', '暂无')}%，归母净利同比 {financial.get('net_profit_yoy_pct', '暂无')}%。",
                        f"   3. 走势：{base.get('pattern_label', '结构待验证')}，近5日 {base.get('return_5d_pct', '暂无')}%，近20日 {base.get('return_20d_pct', '暂无')}%，近60日 {base.get('return_60d_pct', '暂无')}%，量比 {base.get('volume_ratio_5d', '暂无')}。",
                    ]
                )
            lines.append("这是低位启动研究排序，不代表主升浪或收益概率。")
            lines.append(TEXT["safety"])
            return "\n".join(lines)
        candidates = discovery.get("candidates", [])
        target = discovery.get("target_trade_date") or "下一交易日"
        lines = [
            f"基于 {discovery.get('data_as_of')} 收盘数据，{target} 低位挖掘观察池如下："
        ]
        for index, item in enumerate(candidates[:10], start=1):
            themes = item.get("themes") or []
            primary_theme = themes[0] if themes else {}
            catalysts = item.get("news_catalysts") or []
            live = item.get("latest_intelligence") or {}
            financial = live.get("financial_report") or {}
            latest_news = live.get("latest_news") or []
            lines.extend(
                [
                    f"{index}. {item.get('name')}({item.get('symbol')})，研究分 {item.get('score')}。",
                    f"   1. 题材：{primary_theme.get('name', '暂无')}，题材涨幅 {primary_theme.get('change_pct', '暂无')}%。",
                    f"   2. 新闻和财报：{latest_news[0].get('title') if latest_news else (catalysts[0] if catalysts else '暂无明确催化')}；营收同比 {financial.get('operating_income_yoy_pct', '暂无')}%，归母净利同比 {financial.get('net_profit_yoy_pct', '暂无')}%。",
                    f"   3. 走势：{item.get('pattern_label')}，近5日 {item.get('return_5d_pct', '暂无')}%，近20日 {item.get('return_20d_pct', '暂无')}%，近60日 {item.get('return_60d_pct', '暂无')}%，量比 {item.get('volume_ratio_5d', '暂无')}。",
                ]
            )
        if not candidates:
            lines.append("当前没有满足数据与流动性要求的候选。")
        lines.append("该名单先按热门题材和新闻催化圈选，再用财报和低位走势验证，不代表主升浪或收益概率。")
        lines.append(TEXT["safety"])
        return "\n".join(lines)

    if "first_board_ratings" in facts and "limit_up_events" not in facts:
        ratings = facts["first_board_ratings"]
        draft = ratings.get("recommendation_draft") or {}
        draft_candidates = draft.get("candidates") or []
        if draft_candidates:
            lines = [
                f"基于 {draft.get('base_date')} 首板收盘情况，并更新至 "
                f"{str(draft.get('refreshed_at', ''))[:16]} 的一进二盘前草稿如下："
            ]
            lines.extend(
                f"{index}. {item.get('name')}({item.get('symbol')}) "
                f"动态 {item.get('draft_score')} 分，收盘综合 "
                f"{item.get('base_score')} 分（原始规则 "
                f"{item.get('rule_score', item.get('base_score'))} 分），"
                f"收盘后新闻修正 {item.get('news_adjustment', 0):+g}，"
                f"收盘后财报修正 "
                f"{item.get('financial_adjustment', 0):+g}，"
                f"龙虎榜修正 {item.get('dragon_tiger_adjustment', 0):+g}，"
                f"人气变化修正 {item.get('popularity_adjustment', 0):+g}，"
                f"盘后合计 {item.get('dynamic_adjustment', 0):+g}。"
                for index, item in enumerate(draft_candidates, start=1)
            )
            lines.append(
                "收盘前已知事实已计入收盘综合分；盘后只接受新增公告、"
                "新上龙虎榜和可比较的人气变化，且合计修正受限。"
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


def _looks_like_high_score_promotion_question(message: str) -> bool:
    """Return whether the user asks how score-ranked picks promoted to second board."""

    high_score_terms = ("高分票", "高评分", "评分前", "top10", "Top10", "选出的")
    promotion_terms = ("1进2", "一进二", "晋级二板", "二板成功率", "进二板")
    return any(term in message for term in high_score_terms) and any(
        term in message for term in promotion_terms
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
