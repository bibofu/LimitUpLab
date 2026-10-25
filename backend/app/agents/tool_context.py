"""Lossless columnar tool observations for model context only.

Public traces and durable receipts keep the original dictionaries. This formatter
is stateless and requires complete field definitions, so a recovered worker sees
the same facts and units without relying on an in-memory deduplication cache.
"""

from __future__ import annotations

import copy
import json
from typing import Any

FORMAT_KEY = "context_row_encoding"
TABLE_KEY = "rows_table"
FORMAT = "columns_values_v1"
INSTRUCTION = (
    "rows_table.values按原始顺序保留全部行，每行值与columns逐项对应；"
    "missing_columns_by_row记录原来不存在的字段索引，未列出的null是来源明确返回的null；"
    "不得把缺失或null当成0。field_definitions仍是完整字段、单位与口径说明。"
)


class ToolContextError(ValueError):
    """A compact view cannot be safely reconstructed without complete source data."""


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def format_tool_observation(value: dict[str, Any]) -> dict[str, Any]:
    """Remove repeated row keys only when the self-contained view becomes smaller."""
    if "field_definitions_ref" in value or "field_definitions_scope" in value:
        raise ToolContextError("Formatting requires the full receipt, not a deduplicated model view")
    original = copy.deepcopy(value)
    rows = original.get("rows")
    if (not isinstance(rows, list) or len(rows) < 2 or not all(isinstance(row, dict) for row in rows)
            or FORMAT_KEY in original or TABLE_KEY in original):
        return original
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns or not all(isinstance(key, str) for key in columns):
        return original
    values, missing = [], {}
    for offset, row in enumerate(rows):
        values.append([row.get(key) for key in columns])
        absent = [index for index, key in enumerate(columns) if key not in row]
        if absent:
            missing[str(offset)] = absent
    table: dict[str, Any] = {"columns": columns, "values": values}
    if missing:
        table["missing_columns_by_row"] = missing
    compacted = {key: item for key, item in original.items() if key != "rows"}
    compacted[TABLE_KEY] = table
    compacted[FORMAT_KEY] = {"format": FORMAT, "instruction": INSTRUCTION}
    return compacted if _size(compacted) < _size(original) else original


def expand_tool_observation(value: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct exact JSON facts; also used to prove losslessness in tests."""
    original = copy.deepcopy(value)
    if not isinstance(original.get(FORMAT_KEY), dict) or original[FORMAT_KEY].get("format") != FORMAT:
        return original
    table = original.get(TABLE_KEY)
    if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("values"), list):
        raise ToolContextError("Invalid columnar observation")
    columns = table["columns"]
    if len(columns) != len(set(columns)) or not all(isinstance(key, str) for key in columns):
        raise ToolContextError("Column names must be unique strings")
    missing = table.get("missing_columns_by_row", {})
    rows = []
    for offset, values in enumerate(table["values"]):
        if not isinstance(values, list) or len(values) != len(columns):
            raise ToolContextError("Columnar row length does not match its columns")
        absent = missing.get(str(offset), [])
        if any(type(index) is not int or not 0 <= index < len(columns) for index in absent):
            raise ToolContextError("Missing field index is outside the column set")
        rows.append({key: values[index] for index, key in enumerate(columns) if index not in absent})
    return {**{key: item for key, item in original.items() if key not in {FORMAT_KEY, TABLE_KEY}}, "rows": rows}
