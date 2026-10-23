"""Turn validated tool output into a self-describing model-facing result."""

from __future__ import annotations

import json
from typing import Any

from app.agents.react_runtime.evidence import rows_of
from app.agents.tool_output_contracts import field_definitions_for

MAX_VISIBLE_ROWS = 50
_METADATA_KEYS = (
    "data_as_of",
    "trade_date",
    "source",
    "sources",
    "source_truncated",
    "complete",
    "returned_count",
    "matched_count",
    "total_count",
    "data_missing",
)


def dump(value: Any) -> str:
    """Serialize tool observations without exposing non-JSON provider objects."""

    return json.dumps(value, ensure_ascii=False, default=str)


def business_definitions(gateway) -> list[dict[str, Any]]:
    """Return only business tool definitions, never runtime control tools."""

    definitions = []
    for name, tool in gateway.structured.items():
        parameters = tool.args_schema.model_json_schema()
        parameters["additionalProperties"] = False
        definitions.append({"type": "function", "function": {
            "name": name,
            "description": tool.description,
            "parameters": parameters,
        }})
    return definitions


def model_result(name: str, result, payload: Any, status: str) -> dict[str, Any]:
    """Build the compact result the model reads in a ToolMessage."""

    try:
        rows = rows_of(payload, tool=name)
    except ValueError:
        rows = []
    visible = rows[:MAX_VISIBLE_ROWS]
    message: dict[str, Any] = {
        "status": status,
        "summary": result.summary,
        "rows": visible,
        "row_count": len(rows),
        "truncated": len(rows) > MAX_VISIBLE_ROWS,
        "field_definitions": field_definitions_for(
            name, rows=visible, metadata=payload if isinstance(payload, dict) else None
        ),
    }
    if isinstance(payload, dict):
        for key in _METADATA_KEYS:
            if key in payload:
                message[key] = payload[key]
    return message
