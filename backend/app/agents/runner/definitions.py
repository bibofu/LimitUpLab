"""Request-scoped deduplication of model-facing field definitions.

The first result of a tool carries the full ``field_definitions``. Later results
from the same run send only fields whose description is new, and reference the
earlier result for everything already explained. Public tool traces keep the
complete definitions; only the model-facing message is compacted.
"""

from __future__ import annotations

import json
from typing import Any

DEFINITIONS_KEY = "field_definitions"
REFERENCE_KEY = "field_definitions_ref"
SCOPE_KEY = "field_definitions_scope"


class DefinitionDeduplicator:
    """Track which tool field descriptions were already sent in this run."""

    def __init__(self) -> None:
        self._sent: dict[str, dict[str, dict[str, str]]] = {}
        self.saved_chars = 0
        self.compacted_results = 0

    def compact(self, tool: str, value: dict[str, Any]) -> dict[str, Any]:
        """Return a model-facing copy with only previously unsent definitions."""

        definitions = value.get(DEFINITIONS_KEY)
        if not isinstance(definitions, dict):
            return value
        sent = self._sent.setdefault(tool, {})
        fresh: dict[str, Any] = {}
        omitted: dict[str, dict[str, str]] = {}
        for section, fields in definitions.items():
            if not isinstance(fields, dict):
                fresh[section] = fields
                continue
            known = sent.setdefault(section, {})
            for field, text in fields.items():
                description = text if isinstance(text, str) else str(text)
                if known.get(field) == description:
                    omitted.setdefault(section, {})[field] = description
                    continue
                fresh.setdefault(section, {})[field] = text
                known[field] = description
        view = {**value}
        if fresh:
            view[DEFINITIONS_KEY] = fresh
        else:
            view.pop(DEFINITIONS_KEY, None)
        if omitted:
            self.saved_chars += _serialized_size(omitted)
            self.compacted_results += 1
            omitted_count = sum(len(fields) for fields in omitted.values())
            view[REFERENCE_KEY] = (
                f"{omitted_count} 个字段的说明与本 run 中 {tool} 较早结果一致，未重复发送；"
                "字段含义、单位和基期以较早结果为准。"
            )
            if fresh:
                view[SCOPE_KEY] = "仅列出此前未发送的字段"
        return view


def _serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
