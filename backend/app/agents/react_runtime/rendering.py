"""Render declared evidence fields, never model-authored entity lists."""

from html import escape
from math import isfinite

from app.agents.react_runtime.evidence import CURRENT_SCOPE
from app.agents.react_runtime.display_fields import (
    DISPLAY_FIELD_CATALOG, FIELD_UNITS, UNMAPPED_COLUMN_LABEL, evidence_dates, presentation_aliases,
)


RANK_TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平"}


def append_missing_details(answer, missing):
    """Gaps may concern unsupported presentation or delivery, not only data fetching."""
    return answer + "\n\n未完成项：\n" + "\n".join("- " + item for item in missing)


def cell(value, *, field=None, missing_label="缺失"):
    if value is None:
        return missing_label
    if type(value) not in (str, int, float, bool) or (type(value) is float and not isfinite(value)):
        raise ValueError("Table fields must be finite scalar values")
    if field == "rank_trend":
        value = RANK_TREND_LABELS.get(value, f"未识别（{value}）")
    if field == "return_pct" and type(value) in (int, float):
        value = round(value, 4)  # Display only; evidence keeps full threshold precision.
    return escape(str(value)).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def table_prose(answer, marker):
    """Require an unindented, unfenced marker without reinterpreting escapes."""
    before, _, after = answer.partition(marker)
    leading = before.rsplit("\n", 1)[-1]
    trailing = after.split("\n", 1)[0]
    if leading or trailing.strip(" \t\r"):
        raise ValueError(
            "Table placeholder must occupy its own unindented Markdown line. "
            "Use actual newlines after JSON decoding, not literal backslash-n text; "
            "serialize the answer only once and put blank lines around the placeholder."
        )

    # A standalone line inside a fenced code block still renders as code. Track
    # Markdown fence lengths so a shorter or mismatched closer cannot hide it.
    fence, fence_size = None, 0
    for line in before.splitlines():
        content = line.lstrip(" ")
        if len(line) - len(content) > 3 or not content.startswith(("```", "~~~")):
            continue
        char = content[0]
        size = len(content) - len(content.lstrip(char))
        tail = content[size:]
        if fence is None:
            if char != "`" or "`" not in tail:
                fence, fence_size = char, size
        elif char == fence and size >= fence_size and not tail.strip():
            fence, fence_size = None, 0
    if fence is not None:
        raise ValueError("Table placeholder must be outside Markdown code fences")
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    while before_lines and not before_lines[-1].strip(" \t\r\n"):
        before_lines.pop()
    while after_lines and not after_lines[0].strip(" \t\r\n"):
        after_lines.pop(0)
    # Keep content indentation and trailing spaces: they carry Markdown meaning.
    return "".join(before_lines).rstrip("\r\n"), "".join(after_lines)


def render_answer(final, store, output_contract=None):
    marker = "{{evidence_table}}"
    candidate = marker if final.delivery == "table" else final.answer
    if final.table is None:
        if marker in candidate:
            raise ValueError("Table placeholder requires evidence table specification")
        return final.answer
    if candidate.count(marker) != 1:
        raise ValueError("Use exactly one {{evidence_table}} placeholder")
    before, after = table_prose(candidate, marker)
    record = store.get(final.table.evidence_id)
    if store.scope_of(record) != CURRENT_SCOPE or record["result_state"] not in {"ok", "partial", "empty"}:
        raise ValueError("Table requires usable current-run evidence")
    if (final.status == "complete" and record.get("source_truncated")
            and not store.complete_rank_scope(final.table.evidence_id)):
        raise ValueError("Truncated source cannot be delivered as a complete list")
    columns = final.table.columns
    if any(column.field.isascii() and column.label.strip() == column.field for column in columns):
        raise ValueError("Table labels must use readable Chinese meanings, not raw field names")
    if len({c.field for c in columns}) != len(columns):
        raise ValueError("Duplicate table columns")
    rows = record["rows"]
    if len(rows) > 1000:
        raise ValueError("Table exceeds 1000 rows; select an explicit range and disclose the limit")
    headers, annotations = table_metadata(final.table, record, store)
    lines = ["| " + " | ".join(cell(label) for label in headers) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    requested_fields = set(output_contract.fields) if output_contract else set()
    unavailable = output_contract.unavailable_fields if output_contract else {}
    if any(column.field in unavailable for column in columns) and (
        final.status != "partial" or not final.missing
    ):
        raise ValueError("Unbound requested columns require partial with an explicit missing explanation")
    aliases = presentation_aliases(record, store)
    missing_fields = set(DISPLAY_FIELD_CATALOG) | requested_fields | set(aliases.values())
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Table field missing; use exact evidence row fields")
        fields = [c.field if c.field in row else aliases.get(c.field, c.field) for c in columns]
        absent = [field for field in fields if field not in row]
        if absent and (final.status != "partial" or not final.missing or any(
            field not in missing_fields for field in absent
        )):
            raise ValueError("Table field missing; use exact evidence row fields, or retain requested missing columns with partial and an explicit missing explanation")
        values = []
        for column, field in zip(columns, fields):
            value = None if column.field in unavailable else row.get(field)
            rendered = cell(value, field=column.field,
                missing_label=UNMAPPED_COLUMN_LABEL if column.field in unavailable else "数据未成功获取")
            if record["tool"] == "compare_metrics" and field in {"before", "after", "change"} and value is not None and row.get("unit"):
                rendered += " " + cell(row.get("change_unit") if field == "change" else row["unit"])
            values.append(rendered)
        lines.append("| " + " | ".join(values) + " |")
    answer = "\n\n".join(part for part in (before, annotations, "\n".join(lines), after) if part)
    if len(answer) > 60000:
        raise ValueError("Rendered answer exceeds delivery budget")
    return answer


def table_metadata(table, record, store):
    """Render requested annotations from real evidence; never infer from question text."""
    headers = [column.label for column in table.columns]
    notes = []
    if "date" in table.metadata:
        dates = evidence_dates(record, store)
        if not dates:
            raise ValueError("Requested date annotation is unavailable in evidence")
        if record["tool"] != "compare_metrics":
            notes.append("数据日期：" + "、".join(cell(value) for value in dates))
    if "units" in table.metadata:
        if record["tool"] == "compare_metrics":
            if any(not row.get("unit") for row in record["rows"]):
                raise ValueError("Requested metric units are unavailable")
        else:
            numeric = [index for index, column in enumerate(table.columns)
                       if column.field in FIELD_UNITS or any(type(row.get(column.field)) in (int, float) for row in record["rows"])]
            for index in numeric:
                field = table.columns[index].field
                unit = FIELD_UNITS.get(field)
                if unit is None:
                    raise ValueError(f"Requested unit is unavailable for {field}")
                if not headers[index].endswith((f"（{unit}）", f"({unit})")):
                    headers[index] += f"（{unit}）"
    return headers, "\n".join(notes)
