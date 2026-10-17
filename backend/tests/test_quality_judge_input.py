"""Blind, bounded Judge inputs retain exact quotations and explain missing coverage."""

from copy import deepcopy
import json

from app.services.quality_evidence import build_evidence_bundle, canonical_json, content_hash
from app.services.quality_judge_input import build_judge_input


def bundle():
    return build_evidence_bundle({"run_id": "run-a", "session_id": "session-a",
        "request": {"message": "请统计涨停数量并注明来源。"},
        "response": {"answer": "涨停数量为9，来源为公开数据。", "task_status": "complete"},
        "checkpoint": {"runtime": {"evidence": {"ev_a": {"tool": "market_records",
            "payload": {"count": 3, "source": "公开数据"}}}}, "state": {"finish": {"evidence_ids": ["ev_a"]}}},
        "calls": [], "context": [], "assistant_messages": [], "diagnostic_snapshots": [], "delivery_events": []})


def test_input_is_blind_allow_listed_and_stable_without_mutating_the_bundle():
    original = bundle()
    original.update(feedback={"vote": "down"}, reviews=[{"status": "confirmed"}],
                    evaluations=[{"verdict": "fail"}])
    original["diagnostic_snapshots"].append({"private_reasoning": "must stay private"})
    original["request"]["owner_id"] = "sensitive-owner"
    frozen = deepcopy(original)
    first = build_judge_input(original)
    assert first == build_judge_input(original)
    assert original == frozen
    serialized = canonical_json(first)
    assert all(value not in serialized for value in ("vote", "confirmed", "private_reasoning", "sensitive-owner"))
    assert first["input_hash"] == content_hash(first["view"])
    assert first["answer_hash"] == original["manifest"]["answer_hash"]


def test_references_are_server_assigned_and_values_keep_exact_recorded_types():
    view = build_judge_input(bundle())["view"]
    refs = view["evidence_catalog"]
    assert len({entry["ref_id"] for entry in refs}) == len(refs)
    count = next(entry for entry in refs if entry["path"].endswith("/count"))
    assert count["value"] == 3 and type(count["value"]) is int
    assert next(entry for entry in refs if entry["path"].endswith("/tool"))["kind"] == "tool_name"
    assert next(entry for entry in refs if entry["kind"] == "answer")["value"] == bundle()["response"]["answer"]


def test_oversize_core_is_skipped_instead_of_reviewing_a_truncated_answer():
    data = bundle()
    data["response"]["answer"] = "字" * 12000
    result = build_judge_input(data, max_input_chars=4000)
    assert result["skip_reason"] == "answer_exceeds_input_limit"
    assert not any(entry["kind"] == "answer" for entry in result["view"]["evidence_catalog"])
    assert len(canonical_json(result["view"])) <= 4000


def test_bounded_evidence_has_explicit_omissions_and_never_truncates_a_value():
    data = bundle()
    data["checkpoint"]["runtime"]["evidence"]["ev_a"]["payload"]["rows"] = [{"name": "字" * 40, "n": n} for n in range(2000)]
    result = build_judge_input(data, max_input_chars=4000)
    assert result["skip_reason"] is None
    view = result["view"]
    assert len(canonical_json(view)) <= 4000
    assert view["omitted_reference_count"] > 0 and view["limitations"]
    names = [entry["value"] for entry in view["evidence_catalog"] if entry["path"].endswith("/name")]
    assert all(name == "字" * 40 for name in names)


def test_recent_context_remains_ordered_with_older_context_explicitly_omitted():
    data = bundle()
    data["context"] = [{"role": "user", "content": f"前文 {index}"} for index in range(20)]
    view = build_judge_input(data)["view"]
    assert [entry["value"] for entry in view["evidence_catalog"] if entry["kind"] == "context_user"] == [f"前文 {index}" for index in range(8, 20)]
    assert any(item["reason"] == "older_context_omitted" for item in view["limitations"])


def test_input_scrubs_new_credentials_and_preserves_redaction_limitations(monkeypatch):
    data = bundle()
    data["response"]["answer"] = "never-send-secret"
    data["calls"] = [{"result": {"api_key": "private-other", "text": "never-send-secret"}}]
    monkeypatch.setenv("TEST_JUDGE_API_KEY", "never-send-secret")
    result = build_judge_input(data)
    assert "never-send-secret" not in json.dumps(result) and "private-other" not in json.dumps(result)
    assert any(item["reason"] == "credential_redaction" for item in result["view"]["limitations"])


def test_incompatible_schema_has_no_reinterpreted_evidence():
    data = bundle()
    data["manifest"]["schema_version"] = "future"
    result = build_judge_input(data)
    assert result["skip_reason"] == "unsupported_evidence_schema"
    assert not any(entry["kind"] in {"answer", "evidence"} for entry in result["view"]["evidence_catalog"])
