"""Capture a small whitelist from the actual production model input, never an oracle."""

from datetime import date
import hashlib
import json

from langchain_core.messages import SystemMessage


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected a canonical ISO date")
    return value


def _snapshot(messages):
    # Match the production-owned first system message, not user/tool text that
    # happens to contain the context marker. Keep production prompts unchanged.
    from app.agents.react_runtime.runtime import SYSTEM

    prefix = SYSTEM + "\n可信运行上下文："
    first = messages[0] if messages else None
    if not isinstance(first, SystemMessage) or not isinstance(first.content, str):
        return None
    if not first.content.startswith(prefix):
        return None
    raw = json.loads(first.content[len(prefix):])
    dates = raw["available_local_dates"]
    if not isinstance(dates, list):
        raise ValueError("Expected a list of available dates")
    default_date, symbol = raw["page_default_date"], raw["page_default_symbol"]
    if symbol is not None and not isinstance(symbol, str):
        raise ValueError("Expected a string page symbol or null")
    context = {
        "anchor_date": _date(raw["anchor_date"]),
        "page_default_date": _date(default_date) if default_date is not None else None,
        "page_default_symbol": symbol,
        "available_local_dates": [_date(value) for value in dates],
    }
    return {"origin": "agent_system_message",
            "system_message_sha256": hashlib.sha256(first.content.encode("utf-8")).hexdigest(),
            "context": context}


class RuntimeMetadataCapture:
    """Transparent, per-turn provider wrapper with no additional model calls.

    Only completed message requests establish a snapshot. Failed requests and
    budget reservations do not prove that the Agent received their input.
    """

    def __init__(self, provider):
        self.provider = provider
        self.snapshots = []
        self.errors = []

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def generate_messages(self, messages, *args, **kwargs):
        snapshot = error_type = None
        try:
            snapshot = _snapshot(messages)
        except (TypeError, ValueError, KeyError) as error:
            # Do not change the Agent's behavior or persist arbitrary prompt text.
            error_type = type(error).__name__
        result = self.provider.generate_messages(messages, *args, **kwargs)
        if snapshot is not None and snapshot not in self.snapshots:
            self.snapshots.append(snapshot)
        if error_type is not None and error_type not in self.errors:
            self.errors.append(error_type)
        return result
