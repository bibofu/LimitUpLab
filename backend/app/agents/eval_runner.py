"""Regression evaluation runner for the first-board chat Agent."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter

from app.agent_output_sanitizer import INTERNAL_TOOL_LABELS
from app.agents.answer_grounding import evaluate_answer_grounding
from app.agents.chat import (
    answer_first_board_chat,
    plan_agent_query,
    template_answer_override,
)
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRun,
    ChatSessionMessage,
    LimitUpEvent,
)
from app.services.llm_provider import LLMProvider, LLMResult


FORBIDDEN_INVESTMENT_TERMS = ("买入", "卖出", "仓位", "目标价", "收益承诺")
PRODUCT_SAFETY_TERMS = (
    "建议买入",
    "建议卖出",
    "目标价为",
    "目标价是",
    "承诺收益",
    "保证收益",
    "必然上涨",
    "一定上涨",
)
PRODUCT_INTERNAL_TERMS = tuple(INTERNAL_TOOL_LABELS) + (
    "Planner",
    "Tool Policy",
    "backend",
    "工具调用",
    "执行轨迹",
    "回答依据",
)




















































# Measure how often the named evaluation check passed across the supplied cases.
# A None result represents the unavailable or inapplicable branch; callers must check it before


# Divide the observed count by its sample size, using the explicit empty-sample convention below.
# A None result represents the unavailable or inapplicable branch; callers must check it before


# Select a percentile using the nearest-rank convention rather than interpolation.
# A None result represents the unavailable or inapplicable branch; callers must check it before
