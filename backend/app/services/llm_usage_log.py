"""Opt-in per-call LLM usage diagnostics.

When ``LIMITUPLAB_LLM_USAGE_LOG`` names a file, every provider call appends one
JSON line with token counts, cache hits and the caller-supplied context (round,
tool-definition size, message size). Diagnostics never carry prompt text and
never raise into the request path.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

LOG_ENV = "LIMITUPLAB_LLM_USAGE_LOG"
_write_lock = threading.Lock()


@dataclass
class LLMCallUsage:
    """One logical provider call and the facts known about its request."""

    model: str
    context: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    duration_ms: int = 0
    response_mode: str = "text"
    status: str = "pending"


def log_llm_call(scope_id: str, record: LLMCallUsage) -> None:
    """Append one diagnostic line; disabled or unwritable logs stay silent."""

    path = os.getenv(LOG_ENV, "").strip()
    if not path:
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scope_id": scope_id,
        "model": record.model,
        "status": record.status,
        "response_mode": record.response_mode,
        "duration_ms": record.duration_ms,
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
        "total_tokens": record.total_tokens,
        "cached_tokens": record.cached_tokens,
        "cache_hit_ratio": _ratio(record.cached_tokens, record.prompt_tokens),
        "context": record.context,
    }
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with _write_lock:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except (OSError, TypeError, ValueError):
        return None


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)
