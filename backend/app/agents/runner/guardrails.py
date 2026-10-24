"""Small deterministic guardrails for the lightweight runner."""

from __future__ import annotations

MAX_MODEL_ROUNDS = 6
MAX_TOOL_CALLS = 8
DEADLINE_SECONDS = 90

# These phrases are review signals, not verdicts: negatives, quotations and
# historical disclosures can legitimately contain exactly the same words.
ADVICE_REVIEW_SIGNALS = (
    "建议买入",
    "建议卖出",
    "可以买入",
    "可以卖出",
    "应该买入",
    "应该卖出",
    "加仓",
    "减仓",
    "建仓",
    "仓位建议",
    "目标价",
    "保证收益",
    "稳赚",
    "必涨",
    "必跌",
)

REFUSAL = "我只提供有来源的研究事实，不提供买卖、仓位、目标价、收益承诺或确定性预测。"


def requires_compliance_review(answer: str) -> bool:
    """Escalate potentially actionable wording to the semantic reviewer."""

    return any(phrase in (answer or "") for phrase in ADVICE_REVIEW_SIGNALS)


def derived_status(answer: str, tool_statuses: list[str], error: str | None = None) -> str:
    """Map tool outcomes and the final text to one small answer status."""

    if error or not (answer or "").strip():
        return "error"
    if any(status in {"error", "partial"} for status in tool_statuses):
        return "partial"
    return "answered"
