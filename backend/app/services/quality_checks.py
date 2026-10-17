"""Three-state structural assessments of immutable evidence; no prose heuristics."""

from collections import defaultdict
from datetime import date
import hashlib

from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import EVIDENCE_VERSION, EvidenceStore
from app.quality_evaluation_models import EvaluationReport, aggregate_verdict
from app.services.quality_check_support import EVIDENCE_PATH, ancestry, budget, gaps, result, unknown
from app.services.quality_replay_checks import compute_checks, rendering_check


COUNT_TOOLS = {"limit_up_events", "market_event_pool", "remote_limit_up_pool", "dragon_tiger_list", "compute_result"}
EXACT_DATE_TOOLS = COUNT_TOOLS - {"compute_result"}


def _canonical_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _shape_problem(bundle, deadline):
    """Old JSON can decode successfully while still lacking supported structure."""
    for key in ("manifest", "response", "checkpoint"):
        if bundle.get(key) is not None and not isinstance(bundle[key], dict):
            return "/" + key
    manifest = bundle.get("manifest") or {}
    for key in ("missing", "omissions"):
        items = manifest.get(key, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("path", ""), (str, list)) for item in items):
            return "/manifest/" + key
    for key in ("assistant_messages", "renderings", "provenance"):
        items = bundle.get(key)
        if items is not None and (not isinstance(items, list) or any(not isinstance(item, dict) for item in items)):
            return "/" + key
    checkpoint = bundle.get("checkpoint") or {}
    for key in ("runtime", "state"):
        if checkpoint.get(key) is not None and not isinstance(checkpoint[key], dict):
            return "/checkpoint/" + key
    records = (checkpoint.get("runtime") or {}).get("evidence")
    if records is not None and not isinstance(records, dict):
        return EVIDENCE_PATH
    for key, record in (records or {}).items():
        budget(deadline)
        if not isinstance(record, dict):
            return EVIDENCE_PATH + "/" + key
        for field in ("tool", "result_state", "schema_version", "evidence_scope"):
            if record.get(field) is not None and not isinstance(record[field], str):
                return EVIDENCE_PATH + "/" + key + "/" + field
        if record.get("arguments") is not None and not isinstance(record["arguments"], dict):
            return EVIDENCE_PATH + "/" + key + "/arguments"
    return None


def _persistence(bundle, deadline):
    response = bundle.get("response") or {}
    answer = response.get("answer")
    check_id, dimension = "saved_answer_consistency", "persistence"
    if not isinstance(answer, str) or gaps(bundle, "/response/answer"):
        return unknown(check_id, dimension, "missing_or_omitted_saved_answer")
    comparisons, absent = {}, []
    messages = bundle.get("assistant_messages")
    if not isinstance(messages, list) or not messages or gaps(bundle, "/assistant_messages"):
        absent.append("/assistant_messages")
    else:
        for index, message in enumerate(messages):
            budget(deadline)
            path = f"/assistant_messages/{index}"
            if message.get("run_id") != bundle.get("run_id") or message.get("role") != "assistant":
                absent.append(path)
            elif isinstance(message.get("content"), str):
                comparisons[path + "/content"] = message["content"]
            else:
                absent.append(path + "/content")
    # A persisted final answer disagreement must not be hidden by delivered_finish.
    if response.get("stop_reason") == "answered":
        checkpoint_answer = ((bundle.get("checkpoint") or {}).get("runtime") or {}).get("answer")
        if isinstance(checkpoint_answer, str) and not gaps(bundle, "/checkpoint/runtime/answer"):
            comparisons["/checkpoint/runtime/answer"] = checkpoint_answer
        else:
            absent.append("/checkpoint/runtime/answer")
    mismatches = [{"path": path, "expected": answer, "actual": value}
                  for path, value in comparisons.items() if value != answer]
    if response.get("stop_reason") == "answered" and not gaps(bundle, "/checkpoint/runtime/status"):
        saved_status = ((bundle.get("checkpoint") or {}).get("runtime") or {}).get("status")
        if saved_status is not None and saved_status != response.get("task_status"):
            mismatches.append({"path": "/checkpoint/runtime/status", "expected": response.get("task_status"), "actual": saved_status})
    captured_hash = (bundle.get("manifest") or {}).get("captured_answer_hash")
    digest = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    if not captured_hash:
        absent.append("/manifest/captured_answer_hash")
    elif captured_hash != digest:
        mismatches.append({"path": "/manifest/captured_answer_hash", "expected": digest, "actual": captured_hash})
    detail = {"answer_path": "/response/answer", "compared_paths": list(comparisons),
              "mismatches": mismatches, "unavailable_paths": absent}
    if not mismatches and absent:
        return unknown(check_id, dimension, "missing_saved_answer_copies", detail)
    return result(check_id, dimension, "fail" if mismatches else "pass", "已保存答案副本一致性检查完成。", detail)


def _finish_checks(bundle, final, records, references, deadline):
    runtime = bundle["checkpoint"]["runtime"]
    invalid, unavailable = [], []
    for key in references:
        budget(deadline)
        record = records.get(key)
        if gaps(bundle, EVIDENCE_PATH + "/" + key):
            unavailable.append(key)
        elif not isinstance(record, dict) or EvidenceStore.scope_of(record) != "current_run":
            invalid.append(key)
        elif record.get("schema_version") != EVIDENCE_VERSION:
            unavailable.append(key)
    no_required_evidence = runtime.get("requires_current_evidence") is True and not references and final.get("status") in {"complete", "partial", "empty"}
    detail = {"path": "/checkpoint/state/finish", "evidence_ids": references,
              "invalid_ids": invalid, "unknown_ids": unavailable,
              "required_evidence_absent": no_required_evidence}
    reference_check = (result("invalid_evidence_reference", "references", "fail", "已交付候选引用缺失或非本轮证据。", detail)
                       if invalid or no_required_evidence else
                       unknown("invalid_evidence_reference", "references", "unsupported_or_omitted_references", detail)
                       if unavailable else result("invalid_evidence_reference", "references", "pass", "声明引用均属于本轮已保存证据。", detail))
    status, missing = final.get("status"), final.get("missing")
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        consistency = unknown("finish_status_consistency", "finish_contract", "missing_structured_missing_list", detail)
    else:
        violations = []
        if any(not item.strip() for item in missing):
            violations.append("blank_missing_item")
        if status == "complete" and missing:
            violations.append("complete_with_declared_missing")
        if status == "partial" and not missing:
            violations.append("partial_without_declared_missing")
        states = [records[key].get("result_state") for key in references if isinstance(records.get(key), dict)]
        usable_states = all(state in {"ok", "empty", "partial", "error"} for state in states)
        if not invalid and not unavailable and usable_states:
            if status == "empty" and (not states or "empty" not in states or any(state in {"error", "partial"} for state in states)):
                violations.append("empty_without_successful_empty_evidence")
            if status == "complete" and runtime.get("requires_current_evidence") is True and states and set(states) == {"error"}:
                violations.append("complete_using_only_error_evidence")
        status_detail = {"path": "/checkpoint/state/finish", "status": status, "missing": missing,
                         "cited_states": states, "violations": violations,
                         "scope": "declared_contract_only_not_real_task_completeness"}
        consistency = (unknown("finish_status_consistency", "finish_contract", "unavailable_cited_result_states", status_detail)
                       if not violations and (invalid or unavailable or not usable_states) else
                       result("finish_status_consistency", "finish_contract", "fail" if violations else "pass",
                              "交付状态与结构化缺失说明的一致性检查完成。", status_detail))
    return [reference_check, consistency]


def _record_checks(bundle, key, record, deadline):
    budget(deadline)
    path = EVIDENCE_PATH + "/" + key
    detail = {"path": path, "evidence_id": key, "tool": record.get("tool")}
    reason = None
    if gaps(bundle, path):
        reason = "omitted_evidence"
    elif record.get("schema_version") != EVIDENCE_VERSION:
        reason = "unsupported_evidence_version"
    elif EvidenceStore.scope_of(record) != "current_run":
        reason = "historical_evidence_is_not_current_answer_proof"
    elif record.get("source_truncated") or record.get("result_state") not in {"ok", "empty"}:
        reason = "incomplete_source_evidence"
    rows, payload, tool = record.get("rows"), record.get("payload"), record.get("tool")
    if not isinstance(rows, list) or not isinstance(payload, dict):
        reason = reason or "missing_structured_rows_or_payload"
    checks = []
    if tool in COUNT_TOOLS:
        check_id = "evidence_count_mismatch:" + key
        count = payload.get("returned_count") if isinstance(payload, dict) else None
        if reason or type(count) is not int:
            checks.append(unknown(check_id, "counts", reason or "missing_returned_count", detail))
        else:
            checks.append(result(check_id, "counts", "pass" if count == len(rows) else "fail",
                                 "返回条数与完整保存的行数比对完成。",
                                 {**detail, "count_path": path + "/payload/returned_count", "declared_returned_count": count,
                                  "actual_row_count": len(rows), "scope": "returned_rows_not_matched_universe"}))
    if tool == "limit_up_events":
        check_id = "duplicate_rows:" + key
        if reason or not all(isinstance(row, dict) and isinstance(row.get("symbol"), str) and row["symbol"]
                             and _canonical_date(row.get("trade_date")) for row in rows):
            checks.append(unknown(check_id, "row_identity", reason or "missing_stock_date_identity", detail))
        else:
            groups = defaultdict(list)
            for index, row in enumerate(rows):
                budget(deadline)
                groups[(row["symbol"], row["trade_date"])].append(index)
            duplicates = [{"symbol": identity[0], "trade_date": identity[1], "row_indexes": indexes}
                          for identity, indexes in groups.items() if len(indexes) > 1]
            checks.append(result(check_id, "row_identity", "fail" if duplicates else "pass",
                                 "同一股票同一交易日事件唯一性检查完成。", {**detail, "duplicates": duplicates}))
    if tool in EXACT_DATE_TOOLS:
        check_id = "explicit_date_mismatch:" + key
        arguments = record.get("arguments") or {}
        requested, actual = arguments.get("trade_date"), payload.get("trade_date") if isinstance(payload, dict) else None
        date_reason = reason
        if tool == "limit_up_events" and arguments.get("recent_trade_days", 1) != 1:
            date_reason = date_reason or "multi_day_end_bound_is_not_exact_date"
        if not _canonical_date(requested) or not _canonical_date(actual):
            date_reason = date_reason or "missing_explicit_canonical_date"
        detail = {**detail, "paths": [path + "/arguments/trade_date", path + "/payload/trade_date"],
                  "expected": requested, "actual": actual}
        checks.append(unknown(check_id, "date_contract", date_reason, detail) if date_reason else
                      result(check_id, "date_contract", "pass" if requested == actual else "fail",
                             "显式单日查询参数与工具声明日期比对完成。", detail))
    return checks


def evaluate_bundle(bundle, *, deadline=None):
    """Return a validated report; timeout/implementation failures belong to the worker."""
    budget(deadline)
    metadata = {"coverage": {"supported_checks": ["saved_copies", "declared_references", "returned_counts", "stock_date_identity",
                                          "explicit_tool_dates", "finish_contract", "table_rendering", "pure_compute"],
                              "not_assessed": ["semantic_correctness", "market_truth", "actual_user_requirement_completeness",
                                               "agent_runs_output_copy", "post_capture_delivery"]},
                "pass_meaning": "Only the recorded structural check scope passed; not whole-answer correctness."}
    malformed = _shape_problem(bundle, deadline) if isinstance(bundle, dict) else "/"
    if malformed:
        check = unknown("bundle_shape", "evidence_coverage", "unsupported_recorded_structure", {"path": malformed})
        return EvaluationReport(execution_status="skipped", verdict="unknown", checks=[check], metadata=metadata).model_dump()
    if (bundle.get("manifest") or {}).get("schema_version") != "quality-evidence-v1":
        check = unknown("bundle_schema", "evidence_coverage", "unsupported_or_missing_bundle_schema")
        return EvaluationReport(execution_status="skipped", verdict="unknown", checks=[check], metadata=metadata).model_dump()
    checks = [_persistence(bundle, deadline)]
    response, checkpoint = bundle.get("response") or {}, bundle.get("checkpoint") or {}
    runtime, state = checkpoint.get("runtime") or {}, checkpoint.get("state") or {}
    status, final = response.get("task_status"), state.get("finish")
    checks.append(result("terminal_error", "execution", "fail", "运行最终以执行错误结束。",
                         {"path": "/response/task_status", "actual": status, "stop_reason": response.get("stop_reason")})
                  if status == "error" else unknown("terminal_error", "execution", "partial_or_cancelled_is_not_answer_failure",
                       {"task_status": status}) if status in {"partial", "cancelled"} else
                  result("terminal_error", "execution", "pass", "已记录的最终状态没有执行错误。", {"task_status": status})
                  if status in {"complete", "empty", "clarify", "refuse"} else unknown("terminal_error", "execution", "missing_terminal_status"))
    records = runtime.get("evidence")
    version_ok = response.get("generated_by") == VERSION
    linked = (version_ok and response.get("stop_reason") == "answered" and state.get("done") is True
              and runtime.get("status") == status and runtime.get("answer") == response.get("answer")
              and isinstance(final, dict) and final.get("status") == status and isinstance(records, dict)
              and not gaps(bundle, "/checkpoint/state/finish", "/checkpoint/state/done", "/checkpoint/runtime/status",
                           "/checkpoint/runtime/answer", "/response"))
    references = []
    if linked:
        raw_references = final.get("evidence_ids")
        if not isinstance(raw_references, list):
            linked = False
        references = list(raw_references) if isinstance(raw_references, list) else []
        table = final.get("table")
        if isinstance(table, dict):
            references.append(table.get("evidence_id"))
        if any(not isinstance(key, str) or not key for key in references):
            linked = False
    if linked:
        references = list(dict.fromkeys(references))
        checks.extend(_finish_checks(bundle, final, records, references, deadline))
        lineage, reason = ancestry(records, references, deadline)
        if reason:
            checks.append(unknown("evidence_lineage", "evidence_coverage", reason))
        else:
            for key in lineage:
                if isinstance(records.get(key), dict):
                    checks.extend(_record_checks(bundle, key, records[key], deadline))
            checks.extend(compute_checks(bundle, records, lineage, deadline))
    else:
        checks.extend(unknown(check_id, dimension, "no_version_matched_delivered_finish") for check_id, dimension in
                      (("invalid_evidence_reference", "references"), ("finish_status_consistency", "finish_contract"),
                       ("compute_replay", "computation")))
    budget(deadline)
    checks.append(rendering_check(bundle, records if isinstance(records, dict) else {}, deadline) if version_ok else
                  unknown("table_rendering_consistency", "rendering", "unsupported_runtime_version"))
    budget(deadline)
    metadata["coverage"]["recorded_checks"] = [{"check_id": check.check_id, "dimension": check.dimension,
                                                  "verdict": check.verdict} for check in checks]
    return EvaluationReport(execution_status="succeeded", verdict=aggregate_verdict(checks), checks=checks,
                            metadata=metadata).model_dump()
