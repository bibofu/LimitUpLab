"""Conservative evidence-backed delivery when a model call cannot finish.

This is runtime recovery, never a substitute for a successful model finish.
"""
from copy import deepcopy

from app.agents.react_runtime.contracts import Compute, EvidenceColumn, EvidenceTable, Finish
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG, presentation_aliases
from app.agents.react_runtime.evidence import CURRENT_SCOPE, EvidenceStore


def verified_selection_total(store, record, selection):
    """Replay a declared window over complete current inputs, without mutating evidence.

    A returned row count alone cannot prove a ranked window. Every computed
    ancestor must replay exactly, and no earlier slice may hide part of the
    selected collection. The total belongs to this selection's filtered scope,
    never to an unrelated count query or an assumed market-wide universe.
    """
    if record["tool"] != "compute_result":
        return None
    temporary, visiting, verified = EvidenceStore(), set(), {}

    def replay(key, *, final=False):
        if key in visiting:
            raise ValueError("Cyclic evidence lineage")
        if key in verified:
            return verified[key]
        visiting.add(key)
        item = store.get(key)
        payload, rows = item["payload"], item["rows"]
        if (store.scope_of(item) != CURRENT_SCOPE or item["result_state"] not in {"ok", "empty"}
                or item.get("source_truncated") or item.get("data_missing")
                or not isinstance(payload, dict) or payload.get("data_fresh") is False
                or payload.get("source_errors") or payload.get("source_truncated")
                or payload.get("truncated") or payload.get("data_missing")
                or any(not isinstance(row, dict) for row in rows)):
            raise ValueError("Range recovery requires complete current inputs")
        if item["tool"] == "compute_result":
            spec = Compute.model_validate(item["arguments"])
            inputs = [spec.evidence_id]
            if spec.operation in {"intersection", "difference", "union"}:
                inputs.append(spec.other_id)
            if (payload.get("operation") != spec.model_dump()
                    or payload.get("source_evidence_ids") != inputs or item.get("sources") != inputs):
                raise ValueError("Computation lineage is inconsistent")
            if final and (spec.operation != "select" or not spec.sort_by
                    or spec.offset != selection.offset or spec.limit != selection.limit):
                raise ValueError("Selected window does not match the requested range")
            for parent in inputs:
                replay(parent)
            replayed = temporary.get(temporary.compute(spec))
            total = replayed["payload"]["matched_count"]
            if (rows != replayed["rows"] or item["result_state"] != replayed["result_state"]
                    or payload.get("matched_count") != total
                    or payload.get("returned_count") != len(rows)):
                raise ValueError("Stored selection does not match deterministic replay")
            if final:
                if spec.offset >= total:
                    raise ValueError("Selected range lies outside the complete input")
            elif spec.offset != 0 or len(rows) != total:
                raise ValueError("An earlier slice does not prove the complete input")
        else:
            total = payload.get("matched_count")
            if total is None and payload.get("complete") is True:
                total = payload.get("count")
            if type(total) is not int or total != len(rows):
                raise ValueError("Source does not prove complete collection coverage")
        temporary.records[key] = deepcopy(item)
        verified[key] = total
        visiting.remove(key)
        return total

    try:
        return replay(record["evidence_id"], final=True)
    except (KeyError, TypeError, ValueError):
        return None


def partial_delivery(store, task, evidence_id=None):
    """Rebuild a current table; never reuse an unvalidated candidate's prose."""
    contract = task.output_contract
    if (task.pending_slots or contract.mode == "prose_only"
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
    selection_total = None
    if contract.row_selection is not None:
        if evidence_id is None:
            return None
        selection_total = verified_selection_total(store, record, contract.row_selection)
        if selection_total is None:
            return None
    rows, payload = record["rows"], record["payload"]
    if (not rows or not isinstance(payload, dict)
            or payload.get("data_fresh") is False
            or any(not isinstance(row, dict) for row in rows)):
        return None
    unavailable = contract.unavailable_fields
    if any(field not in DISPLAY_FIELD_CATALOG and field not in unavailable for field in contract.fields):
        return None
    aliases = presentation_aliases(record, store)
    fields = [field if any(field in row for row in rows) else aliases.get(field, field)
              for field in contract.fields]
    if not any(field in row for row in rows for field in fields if field not in unavailable):
        return None
    missing = ["最终解释未能通过全部核验；下表直接展示本轮已获取的数据，不能据此认定已覆盖全部要求。"]
    labels = {"symbol": "代码", "name": "名称"}
    requested_labels = {item.field: item.requested for item in contract.field_requests if item.field}
    columns = [EvidenceColumn(field=field, label=unavailable.get(requested, requested_labels.get(requested,
        labels.get(requested, DISPLAY_FIELD_CATALOG.get(requested, {}).get("meaning", requested))))[:40])
        for requested, field in zip(contract.fields, fields)]
    absent_labels = [column.label for column in columns
                     if column.field in unavailable or any(row.get(column.field) is None for row in rows)]
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
    scope_note = (f"本轮筛选范围内共{selection_total}条，展示{len(rows)}条。\n\n"
                  if selection_total is not None else "")
    answer = (scope_note + "以下为本轮已获取的数据；未获取的字段已在表中注明。\n\n"
              "{{evidence_table}}\n\n" + source_note + "\n\n缺口说明：" + " ".join(missing))
    return Finish(
        status="partial", answer=answer, evidence_ids=[record["evidence_id"]],
        table=EvidenceTable(evidence_id=record["evidence_id"], columns=columns),
        missing=missing,
    )
