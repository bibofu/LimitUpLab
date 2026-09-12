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
    looks_like_promotion_opening_question as _looks_like_promotion_opening_question,
)
from app.models import AgentChatRequest
from app.post_limit_query_contract import default_recent_limit_days


IDEOGRAPHIC_COMMA = "\u3001"
UNANSWERABLE_TEXT = "抱歉，该问题无法回答"
TEXT = {
    "greeting": "你好，我是 LimitUpLab 的首板 Agent。我可以总结今日首板、解释个股评分、分析风险，也可以查询热门股票、财经快讯、个股新闻、个股走势和市场环境。",
    "capability": "我是 LimitUpLab V1 涨停后首板研究 Agent。我使用最新完整收盘数据和日 K 线研究一进二 Top10，以及涨停后的高位回撤、横盘缩量、回撤企稳、断板修复和逐日走势；也可以重算这些形态的历史 D+1 至 D+5 描述性统计，并查询热门股票、财经快讯和个股动态。我不提供盘中实时行情、买卖指令、仓位、目标价或收益承诺。",
    "smalltalk": "我在。你可以直接问首板候选、板块分布、评分理由、风险或个股走势。",
    "prompt_injection": "我不能执行改变系统规则、泄露内部提示或调用未授权工具的指令。你可以继续询问 LimitUpLab 支持的收盘复盘问题。",
    "out_of_scope": UNANSWERABLE_TEXT,
    "unsafe": "我不能给出直接交易指令、资金配比、价格预测或回报承诺。我可以基于结构化数据分析评分理由、风险、板块热度和市场环境。",
    "unknown": UNANSWERABLE_TEXT,
    "safety": "\u4ee5\u4e0a\u4e3a\u57fa\u4e8e\u672c\u5730\u7ed3\u6784\u5316\u6570\u636e\u7684\u590d\u76d8\u5206\u6790\uff0c\u4e0d\u6784\u6210\u4e70\u5356\u5efa\u8bae\u3002",
}
