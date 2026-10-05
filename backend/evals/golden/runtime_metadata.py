"""Capture a small whitelist from the actual production model input, never an oracle."""

from datetime import date
import hashlib
import json

from langchain_core.messages import SystemMessage, ToolMessage


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


def _rating_views(messages):
    """Retain the server-owned count semantics actually delivered to the Agent.

    A provider's arbitrary metadata cannot become a trusted instruction: require
    the exact reviewed EvidenceStore.view contract and retain only its whitelist.
    This describes counts, not whether extra business filters were executed.
    """
    expected = {
        "universe_count": "筛选前涨停事件总体数量，不是入池候选数",
        "returned_candidate_count": "本次返回候选数量；指定symbols时仅代表定向返回，不代表全池",
    }
    snapshots = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in {"first_board_ratings", "read_evidence"}:
            continue
        if not isinstance(message.content, str):
            continue
        try:
            view = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(view, dict) or view.get("tool") != "first_board_ratings":
            continue
        metadata, arguments = view.get("metadata"), view.get("arguments")
        if not isinstance(metadata, dict) or not isinstance(arguments, dict):
            continue
        scope = metadata.get("count_scope")
        if (not isinstance(scope, dict) or set(scope) != {*expected, "symbols_filtered"}
                or any(scope.get(key) != value for key, value in expected.items())
                or type(scope.get("symbols_filtered")) is not bool
                or scope["symbols_filtered"] != bool(arguments.get("symbols"))):
            continue
        count = metadata.get("returned_candidate_count")
        key = view.get("evidence_id")
        if (type(count) is not int or count < 0 or type(view.get("row_count")) is not int
                or count != view["row_count"] or not isinstance(key, str) or not 1 <= len(key) <= 128
                or view.get("evidence_scope") != "current_run" or view.get("historical_reference") is not False):
            continue
        retained = {"returned_candidate_count": count, "count_scope": scope}
        if type(view.get("source_truncated")) is bool:
            retained["source_truncated"] = view["source_truncated"]
        exclusions = metadata.get("filtered_out")
        # EvidenceStore.view compacts this known metadata list to four rows and
        # a structural omission marker. Record what was seen, not a claim that
        # the upstream source is incomplete, nor arbitrary payload instructions.
        if isinstance(exclusions, list) and len(exclusions) == 5:
            marker = exclusions[-1]
            if (isinstance(marker, dict) and set(marker) == {"truncated_items"}
                    and type(marker["truncated_items"]) is int and marker["truncated_items"] > 0
                    and all(isinstance(row, dict) and row.get("included") is False
                            and "truncated_items" not in row for row in exclusions[:-1])):
                retained["preview_omissions"] = [{"path": ["metadata", "filtered_out"],
                    "visible_items": 4, "omitted_items": marker["truncated_items"]}]
        snapshots.append({"origin": "agent_evidence_view",
            "tool_message_sha256": hashlib.sha256(message.content.encode("utf-8")).hexdigest(),
            "evidence_id": key, "tool": "first_board_ratings",
            "metadata": retained})
    return snapshots


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
        views = []
        try:
            snapshot = _snapshot(messages)
            if snapshot is not None:
                views = _rating_views(messages)
        except (TypeError, ValueError, KeyError) as error:
            # Do not change the Agent's behavior or persist arbitrary prompt text.
            error_type = type(error).__name__
        result = self.provider.generate_messages(messages, *args, **kwargs)
        for item in ([snapshot] if snapshot is not None else []) + views:
            if item not in self.snapshots:
                self.snapshots.append(item)
        if error_type is not None and error_type not in self.errors:
            self.errors.append(error_type)
        return result
