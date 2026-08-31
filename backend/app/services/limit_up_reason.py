"""Normalize Tonghuashun limit-up reasons for event facts and peer counts."""

from __future__ import annotations

import re

from app.collectors.hithink_finance_collector import HithinkLimitUpPoolSnapshot
from app.models import LimitUpEvent


_REASON_SEPARATOR = re.compile(r"[+＋/／、|｜]+")


def merge_limit_up_reasons(
    events: list[LimitUpEvent],
    snapshot: HithinkLimitUpPoolSnapshot,
) -> tuple[list[LimitUpEvent], int]:
    """Attach structured provider reasons to matching closed limit-up events."""

    reasons: dict[str, str] = {}
    for item in snapshot.items:
        reason = _normalized_reason(item.limit_up_reason)
        if item.symbol and reason:
            reasons[item.symbol] = reason
    merged: list[LimitUpEvent] = []
    enriched_count = 0
    for event in events:
        reason = reasons.get(event.symbol) if event.closed_limit else None
        if reason and reason != event.concept:
            merged.append(event.model_copy(update={"concept": reason}))
            enriched_count += 1
        else:
            merged.append(event)
    return merged, enriched_count


def limit_reason_tokens(value: str) -> frozenset[str]:
    """Split one provider reason into stable labels for same-day peer matching."""

    normalized = _normalized_reason(value)
    if not normalized:
        return frozenset()
    return frozenset(
        token
        for item in _REASON_SEPARATOR.split(normalized)
        if (token := item.strip()) and len(token) >= 2
    )


def count_reason_peers(event: LimitUpEvent, events: list[LimitUpEvent]) -> int:
    """Count closed stocks sharing at least one explicit reason label."""

    target = limit_reason_tokens(event.concept)
    if not target:
        return 0
    return sum(
        1
        for item in events
        if item.closed_limit and target.intersection(limit_reason_tokens(item.concept))
    )


def _normalized_reason(value: str | None) -> str:
    return "+".join(limit_reason_tokens_raw(value))


def limit_reason_tokens_raw(value: str | None) -> list[str]:
    """Return ordered reason labels without recursively normalizing the input."""

    text = " ".join(str(value or "").split())
    if not text:
        return []
    return list(
        dict.fromkeys(
            token
            for item in _REASON_SEPARATOR.split(text)
            if (token := item.strip()) and len(token) >= 2
        )
    )
