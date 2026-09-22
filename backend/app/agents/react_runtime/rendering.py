"""Render declared evidence fields, never model-authored entity lists."""

from html import escape
from math import isfinite

from app.agents.react_runtime.evidence import CURRENT_SCOPE


def cell(value):
    if value is None:
        return "缺失"
    if type(value) not in (str, int, float, bool) or (type(value) is float and not isfinite(value)):
        raise ValueError("Table fields must be finite scalar values")
    return escape(str(value)).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def render_answer(final, store):
    marker = "{{evidence_table}}"
    if final.table is None:
        if marker in final.answer:
            raise ValueError("Table placeholder requires evidence table specification")
        return final.answer
    if final.answer.count(marker) != 1:
        raise ValueError("Use exactly one {{evidence_table}} placeholder")
    record = store.get(final.table.evidence_id)
    if store.scope_of(record) != CURRENT_SCOPE or record["result_state"] not in {"ok", "partial", "empty"}:
        raise ValueError("Table requires usable current-run evidence")
    if (final.status == "complete" and record.get("source_truncated")
            and not store.complete_rank_scope(final.table.evidence_id)):
        raise ValueError("Truncated source cannot be delivered as a complete list")
    columns = final.table.columns
    if len({c.field for c in columns}) != len(columns):
        raise ValueError("Duplicate table columns")
    rows = record["rows"]
    if len(rows) > 1000:
        raise ValueError("Table exceeds 1000 rows; select an explicit range and disclose the limit")
    lines = ["| " + " | ".join(cell(c.label) for c in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        if not isinstance(row, dict) or any(c.field not in row for c in columns):
            raise ValueError("Table field missing; use exact evidence row fields")
        lines.append("| " + " | ".join(cell(row[c.field]) for c in columns) + " |")
    answer = final.answer.replace(marker, "\n".join(lines))
    if len(answer) > 60000:
        raise ValueError("Rendered answer exceeds delivery budget")
    return answer
