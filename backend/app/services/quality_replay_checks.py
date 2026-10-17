"""Replay only local, version-matched pure functions over frozen evidence."""

from copy import deepcopy

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EVIDENCE_VERSION, EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import OutputContract
from app.services.quality_check_support import EVIDENCE_PATH, ancestry, budget, gaps, result, unknown
from app.services.quality_trace import component_fingerprints


def _complete(bundle, records, keys, deadline):
    lineage, reason = ancestry(records, keys, deadline)
    if reason:
        return lineage, reason
    for key in lineage:
        budget(deadline)
        record = records.get(key)
        if not isinstance(record, dict) or gaps(bundle, EVIDENCE_PATH + "/" + key):
            return lineage, "missing_or_omitted_evidence"
        if record.get("schema_version") != EVIDENCE_VERSION:
            return lineage, "unsupported_evidence_version"
        if record.get("source_truncated") or record.get("data_missing"):
            return lineage, "incomplete_source_evidence"
        if record.get("result_state") not in {"ok", "empty"} or EvidenceStore.scope_of(record) != "current_run":
            return lineage, "incomplete_or_historical_source"
        if not isinstance(record.get("rows"), list) or any(not isinstance(row, dict) for row in record["rows"]):
            return lineage, "missing_structured_rows"
    return lineage, None


def compute_checks(bundle, records, keys, deadline=None):
    fingerprints = component_fingerprints()
    provenances = bundle.get("provenance") or []
    version_ok = bool(provenances and fingerprints["compute_sha256"] and all(
        item.get("compute_sha256") == fingerprints["compute_sha256"] for item in provenances))
    checks = []
    for key in keys:
        budget(deadline)
        record = records.get(key)
        if not isinstance(record, dict) or record.get("tool") != "compute_result":
            continue
        check_id, dimension = "compute_replay:" + key, "computation"
        detail = {"path": EVIDENCE_PATH + "/" + key, "evidence_id": key}
        lineage, reason = _complete(bundle, records, [key], deadline)
        detail["parent_evidence_paths"] = [EVIDENCE_PATH + "/" + parent for parent in lineage if parent != key]
        if not version_ok or gaps(bundle, "/provenance"):
            reason = "missing_or_mismatched_compute_fingerprint"
        if reason:
            checks.append(unknown(check_id, dimension, reason, detail))
            continue
        operation = (record.get("payload") or {}).get("operation")
        if not isinstance(operation, dict):
            checks.append(unknown(check_id, dimension, "missing_compute_operation", detail))
            continue
        try:
            spec = Compute.model_validate(operation)
            store = EvidenceStore()
            store.records = deepcopy({parent: records[parent] for parent in lineage})
            computed = store.get(store.compute(spec))
            budget(deadline)
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            detail["error_type"] = type(error).__name__
            checks.append(unknown(check_id, dimension, "recorded_operation_not_replayable", detail))
            continue
        fields = ("rows", "payload", "result_state", "source_truncated", "data_missing", "evidence_scope",
                  "historical_reference", "arguments", "sources")
        mismatches = [{"path": detail["path"] + "/" + field,
                       "expected": computed.get(field), "actual": record.get(field)}
                      for field in fields if computed.get(field) != record.get(field)]
        detail.update(operation=operation, compared_fields=list(fields), mismatches=mismatches)
        checks.append(result(check_id, dimension, "fail" if mismatches else "pass",
                             "同版本纯计算结果与保存的派生证据比对完成。", detail))
    if not checks:
        checks.append(unknown("compute_replay", "computation", "no_delivered_compute_evidence"))
    return checks


def rendering_check(bundle, records, deadline=None):
    check_id, dimension = "table_rendering_consistency", "rendering"
    renderings = bundle.get("renderings") or []
    response = bundle.get("response") or {}
    detail = {"paths": ["/renderings", "/response/answer"]}
    if response.get("task_status") == "cancelled":
        return unknown(check_id, dimension, "user_cancelled_before_delivery", detail)
    if not renderings or gaps(bundle, "/renderings", "/response/answer"):
        return unknown(check_id, dimension, "missing_or_omitted_render_snapshot", detail)
    snapshot = renderings[-1]
    expected_hash = component_fingerprints()["renderer_sha256"]
    if not expected_hash or snapshot.get("renderer_sha256") != expected_hash:
        return unknown(check_id, dimension, "mismatched_renderer_fingerprint", detail)
    raw = snapshot.get("finish_input")
    if not isinstance(raw, dict) or not isinstance(raw.get("table"), dict):
        return unknown(check_id, dimension, "no_structured_table", detail)
    table_id = raw["table"].get("evidence_id")
    if not isinstance(table_id, str):
        return unknown(check_id, dimension, "missing_table_evidence_id", detail)
    lineage, reason = _complete(bundle, records, [table_id], deadline)
    detail.update(evidence_paths=[EVIDENCE_PATH + "/" + key for key in lineage], snapshot_index=len(renderings) - 1)
    if reason:
        return unknown(check_id, dimension, reason, detail)
    if snapshot.get("origin") not in {"gate", "runtime_recovery"} or not isinstance(snapshot.get("output_contract"), dict):
        return unknown(check_id, dimension, "unknown_renderer_origin_or_contract", detail)
    try:
        final = Finish.model_validate(raw)
        store = EvidenceStore()
        store.records = deepcopy({key: records[key] for key in lineage})
        expected = render_answer(final, store, OutputContract.model_validate(snapshot["output_contract"]))
        if snapshot["origin"] == "gate" and final.status == "partial":
            expected += "\n\n数据未成功获取：\n" + "\n".join("- " + item for item in final.missing)
        budget(deadline)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        detail["error_type"] = type(error).__name__
        return unknown(check_id, dimension, "recorded_render_input_not_replayable", detail)
    comparisons = {"/renderings/" + str(len(renderings) - 1) + "/rendered_answer": snapshot.get("rendered_answer"),
                   "/response/answer": response.get("answer")}
    mismatches = [{"path": path, "expected": expected, "actual": actual}
                  for path, actual in comparisons.items() if actual != expected]
    detail.update(expected_rendered_answer=expected, mismatches=mismatches,
                  finish_input=raw, output_contract=snapshot["output_contract"])
    return result(check_id, dimension, "fail" if mismatches else "pass",
                  "同版本表格渲染与原始候选、保存答案比对完成。", detail)
