"""Render declared evidence fields, never model-authored entity lists."""

from html import escape
from math import isfinite

from app.agents.react_runtime.evidence import CURRENT_SCOPE
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG, presentation_aliases


RANK_TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平"}


def cell(value, *, field=None, missing_label="缺失"):
    if value is None:
        return missing_label
    if type(value) not in (str, int, float, bool) or (type(value) is float and not isfinite(value)):
        raise ValueError("Table fields must be finite scalar values")
    if field == "rank_trend":
        value = RANK_TREND_LABELS.get(value, f"未识别（{value}）")
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
    if final.table is None:
        if marker in final.answer:
            raise ValueError("Table placeholder requires evidence table specification")
        return final.answer
    if final.answer.count(marker) != 1:
        raise ValueError("Use exactly one {{evidence_table}} placeholder")
    before, after = table_prose(final.answer, marker)
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
    lines = ["| " + " | ".join(cell(c.label) for c in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    requested_fields = set(output_contract.fields) if output_contract else set()
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
        lines.append("| " + " | ".join(cell(row.get(field), field=c.field,
            missing_label="数据未成功获取") for c, field in zip(columns, fields)) + " |")
    answer = "\n\n".join(part for part in (before, "\n".join(lines), after) if part)
    if len(answer) > 60000:
        raise ValueError("Rendered answer exceeds delivery budget")
    return answer
