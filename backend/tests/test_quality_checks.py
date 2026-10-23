"""Offline rules never call providers, reload market data, or inspect prose claims."""

from copy import deepcopy
from time import perf_counter

import pytest

from app.agents.react_runtime.contracts import Compute, Finish, VERSION
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import OutputContract
from app.quality_evaluation_models import EvaluationReport
from app.services.quality_checks import evaluate_bundle
from app.services.quality_evidence import build_evidence_bundle
from app.services.quality_trace import component_fingerprints, rendering_snapshot


def material(*, store=None, final=None, status="complete"):
    final = final or {"status": status, "answer": "数量999，000001出现两次。", "evidence_ids": [], "missing": []}
    answer = final["answer"]
    return {"run_id": "run-test", "session_id": "session-test", "started_at": "2026-10-17T00:00:00Z",
            "request": {"message": "研究问题"}, "response": {"answer": answer, "task_status": status,
            "generated_by": VERSION, "stop_reason": "answered"},
            "checkpoint": {"runtime": {"answer": answer, "status": status, "requires_current_evidence": False,
                                        "evidence": store.records if store else {}},
                           "state": {"done": True, "finish": final}},
            "calls": [], "context": [], "delivery_events": [],
            "assistant_messages": [{"message_id": "answer_run-test", "run_id": "run-test", "role": "assistant", "content": answer}],
            "diagnostic_snapshots": [{"id": 1, "stage": "provenance", "payload": {
                "runtime_version": VERSION, **component_fingerprints()}, "omissions": []}]}


def evaluate(value):
    report = evaluate_bundle(build_evidence_bundle(value))
    EvaluationReport.model_validate(report)
    return {check["check_id"]: check for check in report["checks"]}


def source(store, *, rows=None, tool="limit_up_events", arguments=None, payload=None):
    rows = rows if rows is not None else [{"symbol": "000001", "name": "股票甲", "trade_date": "2026-10-16", "amount": 100}]
    collection = "events" if tool == "limit_up_events" else "items"
    return store.add(tool=tool, state="ok" if rows else "empty",
                     arguments=arguments if arguments is not None else {"trade_date": "2026-10-16", "recent_trade_days": 1},
                     payload={collection: rows, "returned_count": len(rows), "matched_count": len(rows),
                              "trade_date": "2026-10-16", **(payload or {})})


def cited(store, key, **kwargs):
    final = {"status": "complete", "answer": "声明数量999也不扫描正文。", "evidence_ids": [key], "missing": []}
    final.update(kwargs)
    return material(store=store, final=final, status=final["status"])


def table_material(*, computed=False, status="complete", origin="gate"):
    store = EvidenceStore()
    key = source(store)
    if computed:
        key = store.compute(Compute(evidence_id=key, operation="select"))
    final = Finish(status=status, answer="{{evidence_table}}", evidence_ids=[key],
                   missing=["独立研究字段未取得"] if status == "partial" else [],
                   table={"evidence_id": key, "columns": [{"field": "symbol", "label": "代码"}]})
    contract = OutputContract()
    answer = render_answer(final, store, contract)
    if status == "partial" and origin == "gate":
        answer += "\n\n未完成项：\n- 独立研究字段未取得"
    value = material(store=store, final=final.model_dump(), status=status)
    value["response"]["answer"] = value["checkpoint"]["runtime"]["answer"] = value["assistant_messages"][0]["content"] = answer
    value["diagnostic_snapshots"].append({"id": 2, "stage": "answer_rendering", "omissions": [],
        "payload": rendering_snapshot(final, store, contract, answer, origin=origin)})
    return value, key


def test_successful_fields_are_not_semantic_approval_and_prose_is_not_scanned():
    report = evaluate_bundle(build_evidence_bundle(material()))
    assert report["verdict"] == "unknown"
    assert report["execution_status"] == "succeeded"
    assert "semantic_correctness" in report["metadata"]["coverage"]["not_assessed"]
    assert all(check["verdict"] != "fail" for check in report["checks"])


@pytest.mark.parametrize("copy_path", ["checkpoint", "assistant", "hash"])
def test_persistence_mismatch_not_hidden_by_delivered_finish(copy_path):
    value = material()
    if copy_path == "checkpoint":
        value["checkpoint"]["runtime"]["answer"] = "不同副本"
    elif copy_path == "assistant":
        value["assistant_messages"][0]["content"] = "不同副本"
    bundle = build_evidence_bundle(value)
    if copy_path == "hash":
        bundle["manifest"]["captured_answer_hash"] = "bad"
    report = evaluate_bundle(bundle)
    check = next(check for check in report["checks"] if check["check_id"] == "saved_answer_consistency")
    assert check["verdict"] == report["verdict"] == "fail"
    assert check["evidence"]["mismatches"]


def test_missing_or_redacted_saved_copy_is_unknown():
    value = material()
    value.pop("assistant_messages")
    assert evaluate(value)["saved_answer_consistency"]["verdict"] == "unknown"
    bundle = build_evidence_bundle(material())
    bundle["manifest"]["omissions"].append({"path": "/response", "reason": "credential_redaction_or_depth_limit"})
    assert evaluate_bundle(bundle)["checks"][0]["verdict"] == "unknown"


def test_recovered_intermediate_error_and_user_cancellation_are_not_bad_answers():
    value = material()
    value["calls"] = [{"result": {"result_state": "error"}}, {"result": {"result_state": "ok"}}]
    assert all(check["verdict"] != "fail" for check in evaluate(value).values())
    value["response"].update(task_status="cancelled", stop_reason="cancelled", answer="已取消")
    value["assistant_messages"][0]["content"] = "已取消"
    value["delivery_events"] = [{"event": "interrupted", "answer_hash": ""}]
    assert all(check["verdict"] != "fail" for check in evaluate(value).values())


def test_missing_reference_fails_only_for_linked_current_version_finish():
    value = cited(EvidenceStore(), "missing")
    assert evaluate(value)["invalid_evidence_reference"]["verdict"] == "fail"
    value["response"]["generated_by"] = "react-runtime-v1"
    assert evaluate(value)["invalid_evidence_reference"]["verdict"] == "unknown"
    value["response"]["generated_by"] = VERSION
    value["response"]["stop_reason"] = "validation_failed"
    assert evaluate(value)["invalid_evidence_reference"]["verdict"] == "unknown"


def test_unknown_evidence_version_and_bad_identity_do_not_produce_false_failure():
    store = EvidenceStore()
    key = source(store, rows=[{"symbol": "000001", "trade_date": "未知"}] * 2)
    assert evaluate(cited(store, key))["duplicate_rows:" + key]["verdict"] == "unknown"
    store.records[key]["schema_version"] = "future"
    store.records[key]["payload"]["returned_count"] = 999
    checks = evaluate(cited(store, key))
    assert checks["evidence_count_mismatch:" + key]["verdict"] == "unknown"
    assert checks["invalid_evidence_reference"]["verdict"] == "unknown"


def test_count_contract_is_returned_count_not_matched_count():
    store = EvidenceStore()
    key = source(store)
    value = cited(store, key)
    store.records[key]["payload"]["matched_count"] = 999
    assert evaluate(value)["evidence_count_mismatch:" + key]["verdict"] == "pass"
    store.records[key]["payload"]["returned_count"] = 2
    assert evaluate(value)["evidence_count_mismatch:" + key]["verdict"] == "fail"
    store.records[key]["source_truncated"] = True
    assert evaluate(value)["evidence_count_mismatch:" + key]["verdict"] == "unknown"


def test_identity_is_stock_and_day_not_stock_alone_or_dragon_tiger_reason():
    store = EvidenceStore()
    rows = [{"symbol": "000001", "trade_date": day} for day in ["2026-10-15", "2026-10-16"]]
    key = source(store, rows=rows)
    assert evaluate(cited(store, key))["duplicate_rows:" + key]["verdict"] == "pass"
    rows[1]["trade_date"] = rows[0]["trade_date"]
    assert evaluate(cited(store, key))["duplicate_rows:" + key]["verdict"] == "fail"
    store.records[key]["tool"] = "dragon_tiger_list"
    assert "duplicate_rows:" + key not in evaluate(cited(store, key))


def test_explicit_date_check_excludes_latest_and_multi_day_bounds():
    store = EvidenceStore()
    key = source(store, arguments={"trade_date": "2026-10-15"})
    assert evaluate(cited(store, key))["explicit_date_mismatch:" + key]["verdict"] == "fail"
    store.records[key]["arguments"]["recent_trade_days"] = 2
    assert evaluate(cited(store, key))["explicit_date_mismatch:" + key]["verdict"] == "unknown"
    store.records[key]["arguments"] = {}
    assert evaluate(cited(store, key))["explicit_date_mismatch:" + key]["verdict"] == "unknown"


@pytest.mark.parametrize("status,missing,verdict", [("complete", [], "pass"), ("complete", ["缺失"], "fail"),
    ("partial", [], "fail"), ("partial", ["缺失"], "pass"), ("partial", [" "], "fail")])
def test_finish_status_checks_only_structured_contract(status, missing, verdict):
    store = EvidenceStore()
    key = source(store)
    value = cited(store, key, status=status, missing=missing)
    value["checkpoint"]["runtime"]["requirements"] = [{"description": "Agent自己生成清单", "status": "pending"}]
    assert evaluate(value)["finish_status_consistency"]["verdict"] == verdict


def test_empty_requires_complete_empty_evidence_not_failed_query():
    store = EvidenceStore()
    key = source(store, rows=[])
    assert evaluate(cited(store, key, status="empty"))["finish_status_consistency"]["verdict"] == "pass"
    store.records[key]["result_state"] = "error"
    assert evaluate(cited(store, key, status="empty"))["finish_status_consistency"]["verdict"] == "fail"
    value = cited(store, key)
    value["checkpoint"]["runtime"]["requires_current_evidence"] = True
    assert evaluate(value)["finish_status_consistency"]["verdict"] == "fail"


@pytest.mark.parametrize("status,origin", [("complete", "gate"), ("partial", "gate"), ("partial", "runtime_recovery")])
def test_matching_renderer_includes_correct_partial_suffix(status, origin):
    value, _ = table_material(status=status, origin=origin)
    assert evaluate(value)["table_rendering_consistency"]["verdict"] == "pass"
    value["response"]["answer"] += "篡改"
    assert evaluate(value)["table_rendering_consistency"]["verdict"] == "fail"


def test_renderer_version_source_truncation_and_missing_snapshot_are_unknown():
    value, key = table_material()
    value["diagnostic_snapshots"][-1]["payload"]["renderer_sha256"] = "old"
    assert evaluate(value)["table_rendering_consistency"]["verdict"] == "unknown"
    value, key = table_material()
    value["checkpoint"]["runtime"]["evidence"][key]["source_truncated"] = True
    assert evaluate(value)["table_rendering_consistency"]["verdict"] == "unknown"
    value["diagnostic_snapshots"].pop()
    assert evaluate(value)["table_rendering_consistency"]["verdict"] == "unknown"


@pytest.mark.parametrize("operation,options", [("select", {"sort_by": "amount"}), ("distinct", {}),
    ("aggregate", {"aggregate": "sum", "metric": "amount"}), ("intersection", {}), ("difference", {}), ("union", {})])
def test_pure_compute_replay_matches_all_supported_operations(operation, options):
    store = EvidenceStore()
    left, right = source(store), source(store)
    spec = Compute(evidence_id=left, other_id=right, operation=operation, **options)
    key = store.compute(spec)
    value = cited(store, key)
    assert evaluate(value)["compute_replay:" + key]["verdict"] == "pass"
    store.records[key]["rows"] = [{"tampered": 1}]
    check = evaluate(value)["compute_replay:" + key]
    assert check["verdict"] == "fail" and check["evidence"]["parent_evidence_paths"]


@pytest.mark.parametrize("missing", ["parent", "version", "truncated", "cycle", "omitted"])
def test_compute_missing_prerequisites_never_report_false_failure(missing):
    value, key = table_material(computed=True)
    records = value["checkpoint"]["runtime"]["evidence"]
    parent = records[key]["arguments"]["evidence_id"]
    if missing == "parent":
        records.pop(parent)
    elif missing == "version":
        value["diagnostic_snapshots"][0]["payload"]["compute_sha256"] = "old"
    elif missing == "truncated":
        records[parent]["source_truncated"] = True
    elif missing == "cycle":
        records[key]["payload"]["operation"]["evidence_id"] = key
    else:
        value["missing"] = [{"path": "/checkpoint/runtime/evidence/" + parent, "reason": "omitted"}]
    checks = evaluate(value)
    assert all(check["verdict"] != "fail" for name, check in checks.items() if name.startswith("compute_") or name == "evidence_lineage")
    assert any(check["verdict"] == "unknown" for name, check in checks.items() if name.startswith("compute_") or name == "evidence_lineage")


def test_bundle_is_not_mutated_and_deadline_is_cooperative():
    value, _ = table_material(computed=True)
    bundle = build_evidence_bundle(value)
    before = deepcopy(bundle)
    evaluate_bundle(bundle)
    assert bundle == before
    with pytest.raises(TimeoutError):
        evaluate_bundle(bundle, deadline=perf_counter() - 1)
    assert evaluate_bundle({"manifest": {"schema_version": "future"}})["execution_status"] == "skipped"


@pytest.mark.parametrize("path,value", [("checkpoint", {"runtime": [], "state": []}),
    ("assistant_messages", [None]), ("renderings", [[]]), ("provenance", ["bad"]),
    ("manifest", "bad"), ("manifest", {"schema_version": "quality-evidence-v1", "missing": [42]})])
def test_legacy_malformed_nested_json_is_unknown_not_retryable_worker_error(path, value):
    bundle = build_evidence_bundle(material())
    bundle[path] = value
    report = evaluate_bundle(bundle)
    assert report["execution_status"] == "skipped"
    assert report["verdict"] == "unknown"
    assert report["checks"][0]["check_id"] == "bundle_shape"


def test_saved_terminal_status_mismatch_is_not_hidden_by_finish_linkage():
    value = material()
    value["checkpoint"]["runtime"]["status"] = "partial"
    assert evaluate(value)["saved_answer_consistency"]["verdict"] == "fail"
