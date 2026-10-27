"""Narrow deterministic policy for invisible, low-risk memory activation.

The extractor proposes candidates. This module decides whether a candidate is
safe enough to activate without a human review screen. It intentionally starts
with tool-display additions only; updates, deletes, free text and ambiguous
scopes remain review/operator work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.agents.memory_policy import FORBIDDEN_MEMORY_TERMS
from app.services.memory_display import COLUMN_LABELS, normalize_display_columns


MIN_AUTO_CONFIDENCE = 0.9
MAX_AUTO_DISPLAY_COLUMNS = 8
PERSISTENCE_MARKERS = (
    "以后", "今后", "后续", "从此", "每次", "默认", "始终", "一直",
    "记住", "改成", "改为", "always", "default", "from now on",
)
SAFE_AUTO_DISPLAY_TOOLS = frozenset({
    "trading_calendar", "market_summary", "market_index_trend",
    "daily_board_promotion", "sector_performance", "sector_stock_ranking",
    "hot_stock_ranking", "dragon_tiger_list", "finance_news", "stock_news",
    "stock_activity", "stock_profile", "first_board_ratings",
    "market_event_pool", "limit_up_events", "recent_limit_up_returns",
    "stock_kline", "stock_financials", "index_search", "index_quote",
    "index_constituents", "post_limit_screen", "post_limit_path",
    "post_limit_statistics", "review_high_score_picks",
})
DISPLAY_COLUMN_IDS = frozenset(COLUMN_LABELS)


@dataclass(frozen=True)
class SafeAutoDecision:
    eligible: bool
    reasons: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    activation_reason: str | None = None


def _ineligible(*reasons: str) -> SafeAutoDecision:
    return SafeAutoDecision(False, tuple(reasons), "medium")


def evaluate_safe_auto_candidate(
    candidate: Any,
    *,
    key: str,
    message: str,
    consolidation: dict[str, Any],
) -> SafeAutoDecision:
    """Return whether one consolidated candidate may bypass manual review.

    The function is deliberately conservative and does not trust the model's
    prose. It requires a structured display policy, a narrow tool scope, an
    exact source quote and an untouched add target.
    """

    if getattr(candidate, "candidate_action", None) != "upsert":
        return _ineligible("not_an_upsert")
    if consolidation.get("decision") != "add":
        return _ineligible("not_an_add")
    if consolidation.get("target_memory_id") is not None:
        return _ineligible("has_existing_target")
    if consolidation.get("superseded_candidate_id") is not None:
        return _ineligible("replaced_pending_candidate")
    if getattr(candidate, "memory_type", None) != "preference":
        return _ineligible("not_a_preference")
    if float(getattr(candidate, "confidence", 0)) < MIN_AUTO_CONFIDENCE:
        return _ineligible("low_confidence")
    if getattr(candidate, "stability", None) not in {
        "explicit_future", "explicit_correction",
    }:
        return _ineligible("unstable_expression")
    if getattr(candidate, "scope_type", None) != "tool":
        return _ineligible("not_tool_scoped")
    scope = getattr(candidate, "scope", None)
    if not isinstance(scope, dict) or set(scope) != {"tool_name"}:
        return _ineligible("ambiguous_scope")
    tool = str(scope.get("tool_name") or "").strip()
    if tool not in SAFE_AUTO_DISPLAY_TOOLS:
        return _ineligible("unsupported_tool")
    if key != f"tool_display:{tool}":
        return _ineligible("key_scope_mismatch")
    structured = getattr(candidate, "structured", None)
    if not isinstance(structured, dict) or set(structured) != {"display_columns"}:
        return _ineligible("missing_structured_display_policy")
    columns = normalize_display_columns(structured.get("display_columns"))
    if not columns:
        return _ineligible("missing_display_columns")
    if len(columns) > MAX_AUTO_DISPLAY_COLUMNS:
        return _ineligible("too_many_display_columns")
    if any(column not in DISPLAY_COLUMN_IDS for column in columns):
        return _ineligible("unknown_display_column")
    quote = str(getattr(candidate, "source_quote", "") or "")
    if not quote or quote not in message:
        return _ineligible("source_not_verbatim")
    content = str(getattr(candidate, "content_text", "") or "")
    if not content or any(
        term in content.casefold() for term in FORBIDDEN_MEMORY_TERMS
    ):
        return _ineligible("forbidden_content")
    if not any(marker in quote.casefold() for marker in PERSISTENCE_MARKERS):
        return _ineligible("no_persistence_marker")
    return SafeAutoDecision(
        True,
        ("structured_tool_display_add",),
        "low",
        "safe_auto: tool display add with structured columns and explicit future intent",
    )
