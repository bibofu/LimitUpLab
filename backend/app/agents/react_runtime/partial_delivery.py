"""Conservative evidence-backed delivery when a model call cannot finish.

This is runtime recovery, never a substitute for a successful model finish.
"""
from app.agents.react_runtime.contracts import EvidenceColumn, EvidenceTable, Finish
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG


def partial_delivery(store, task):
    """Return a candidate only when one current dataset and its columns are unambiguous."""
    contract = task.output_contract
    if (task.pending_slots or contract.mode == "prose_only" or contract.row_selection is not None
            or not contract.table_required or not contract.fields):
        return None
    current = store.current_records()
    usable = [record for record in current
              if record["result_state"] in {"ok", "partial", "empty"}]
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
    if any(any(field not in row for field in contract.fields) for row in rows):
        return None
    missing = ["模型未能完成最终回答，尚未完成对用户要求的全部核验。"]
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
    source_note = ("本次返回标注来源：" + source if isinstance(source, str) and 0 < len(source) <= 200
                   else "本次返回未提供可直接展示的来源标识。")
    answer = ("本次研究未能正常完成。以下仅保留本轮已取得的数据，尚未完成所需范围与完整性核验。\n\n"
              "{{evidence_table}}\n\n" + source_note + "\n\n缺口说明：" + " ".join(missing))
    labels = {"symbol": "代码", "name": "名称"}
    return Finish(
        status="partial", answer=answer, evidence_ids=[record["evidence_id"]],
        table=EvidenceTable(evidence_id=record["evidence_id"], columns=[
            EvidenceColumn(field=field, label=labels.get(field, DISPLAY_FIELD_CATALOG[field]["meaning"]))
            for field in contract.fields]),
        missing=missing,
    )
