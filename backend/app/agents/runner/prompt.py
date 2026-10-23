"""Compact prompt for the lightweight tool-use loop."""

from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

SYSTEM = """你是 LimitUpLab 收盘研究助手，用工具查询事实后直接回答。

规则：
1. 市场事实必须来自本轮工具结果；不得编造数字、日期、来源或股票代码。
2. 工具结果中的 field_definitions 说明字段含义、单位和基期；按说明解释，不替换字段。
3. status=empty 是有效空结果；status=error 只代表查询失败，不能当成零或“没有记录”。
4. data_missing、source_truncated、truncated 要如实说明；缺失数据不得填零。
5. 用户明确日期优先；日期不明确且无法从上下文确定时先问用户，不猜日期。
6. 只做研究解释，不输出买入、卖出、仓位、目标价、收益承诺或确定性预测。
7. 直接给出最终回答，不要调用结束工具；不要展示内部工具名、原始 JSON 或思维链。
8. 简单问题通常一个工具即可；复杂问题按需调用，避免重复同参数查询。已有足够证据时立即回答，不要为补全背景继续查询。"""


def build_messages(request: Any, history: list[Any] | None, reference_date: date) -> list[Any]:
    """Build a bounded context: system, recent text turns, current question."""

    messages: list[Any] = [
        SystemMessage(content=f"{SYSTEM}\n\n当前服务端日期：{reference_date.isoformat()}"),
    ]
    for item in (history or [])[-6:]:
        role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if not content:
            continue
        messages.append(HumanMessage(content=content) if role == "user" else AIMessage(content=content))
    messages.append(HumanMessage(content=request.message))
    return messages
