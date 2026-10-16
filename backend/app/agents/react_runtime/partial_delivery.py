"""Conservative evidence-backed delivery when a model call cannot finish.

This is runtime recovery, never a substitute for a successful model finish.
"""
from app.agents.react_runtime.contracts import EvidenceColumn, EvidenceTable, Finish
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG, presentation_aliases


def partial_delivery(store, task, evidence_id=None):
    """Rebuild a current table; never reuse an unvalidated candidate's prose."""
    contract = task.output_contract
    if (task.pending_slots or contract.mode == "prose_only" or contract.row_selection is not None
            or not contract.table_required or not contract.fields):
        return None
    current = store.current_records()
    usable = [record for record in current
              if record["result_state"] in {"ok", "partial", "empty"}]
    if evidence_id is not None:
        usable = [record for record in usable if record["evidence_id"] == evidence_id]
    if len(usable) != 1:
        return None
    record = usable[0]
    rows, payload = record["rows"], record["payload"]
    if (not rows or not isinstance(payload, dict)
            or payload.get("data_fresh") is False
            or any(not isinstance(row, dict) for row in rows)):
        return None
    if any(field not in DISPLAY_FIELD_CATALOG for field in contract.fields):
        return None
    aliases = presentation_aliases(record, store)
    fields = [field if any(field in row for row in rows) else aliases.get(field, field)
              for field in contract.fields]
    if not any(field in row for row in rows for field in fields):
        return None
    missing = ["最终解释未能通过全部核验；下表直接展示本轮已获取的数据，不能据此认定已覆盖全部要求。"]
    labels = {"symbol": "代码", "name": "名称"}
    requested_labels = {item.field: item.requested for item in contract.field_requests if item.field}
    columns = [EvidenceColumn(field=field, label=requested_labels.get(requested,
        labels.get(requested, DISPLAY_FIELD_CATALOG[requested]["meaning"]))[:40])
        for requested, field in zip(contract.fields, fields)]
    absent_labels = [column.label for column in columns
                     if any(row.get(column.field) is None for row in rows)]
    if absent_labels:
        missing.append("、".join(absent_labels) + "：数据未成功获取。")
    if record.get("source_truncated"):
        missing.append(f"来源返回被截断；仅展示当前返回的{len(rows)}条，不能视为全部记录或全市场数量。")
    if record.get("data_missing") or payload.get("source_errors"):
        missing.append("部分来源数据缺失，未取得完整结果。")
    elif record["result_state"] == "partial" and not record.get("source_truncated"):
        missing.append("当前证据为部分结果，仍有数据缺口。")
    if any(item["result_state"] == "error" for item in current):
        missing.append("另有查询执行失败，相关结果未取得，不能据此认定没有记录。")
    # Missing-value explanations come from runtime-owned flags, not arbitrary
    # provider strings. Actual source identity is quoted and independently reviewed.
    source = payload.get("source")
    if any(column.field == "source" for column in columns) and any(row.get("source") for row in rows):
        source_note = "来源见表中各条记录；未取得的来源标识已注明。"
    else:
        source_note = ("本次返回标注来源：" + source if isinstance(source, str) and 0 < len(source) <= 200
                       else "本次返回未提供可直接展示的来源标识。")
    answer = ("以下为本轮已获取的数据；未获取的字段已在表中注明。\n\n"
              "{{evidence_table}}\n\n" + source_note + "\n\n缺口说明：" + " ".join(missing))
    return Finish(
        status="partial", answer=answer, evidence_ids=[record["evidence_id"]],
        table=EvidenceTable(evidence_id=record["evidence_id"], columns=columns),
        missing=missing,
    )
