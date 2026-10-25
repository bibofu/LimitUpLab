import pytest

from app.agents.structured_references import references_from_committed_result, resolve_reference


def response(answer="| 股票 | 代码 |\n|---|---|\n|乙公司|000002|\n|甲公司|600001|", **updates):
    return {"task_status": "complete", "run_id": "run-1", "answer": answer,
            "tool_results": [{"name": "ratings", "status": "success", "output": {
                "trade_date": "2026-10-23", "rows": [
                    {"symbol": "600001", "name": "甲公司", "score": 80},
                    {"symbol": "000002", "name": "乙公司", "score": 70},
                ]}}], **updates}


def test_order_comes_from_verified_display_not_source_order():
    refs = references_from_committed_result("run-1", response())
    assert refs[0]["ordered_symbols"] == ["000002", "600001"]
    assert refs[0]["source_symbols"] == ["600001", "000002"]
    assert resolve_reference("第二只最近5日走势怎么样？", refs)["symbols"] == ["600001"]


@pytest.mark.parametrize("query", ["第2只走势", "第二只走势", "第2名情况"])
def test_ordinal_variants(query):
    refs = references_from_committed_result("run-1", response())
    assert resolve_reference(query, refs)["symbols"] == ["600001"]


@pytest.mark.parametrize("status", ["cancelled", "error", "refuse", "clarify"])
def test_uncommitted_or_failed_answers_cannot_promote_references(status):
    assert references_from_committed_result("run-1", response(task_status=status)) == []


def test_prose_does_not_prove_list_order():
    refs = references_from_committed_result("run-1", response("甲公司和乙公司的数据已返回。"))
    assert refs[0]["ordinal_resolvable"] is False
    assert resolve_reference("第二只", refs)["status"] == "clarify"


def test_numbered_list_is_supported_without_guessing_from_summary():
    refs = references_from_committed_result("run-1", response("1. 乙公司\n2. 甲公司"))
    assert resolve_reference("第一只", refs)["symbols"] == ["000002"]


def test_duplicate_display_and_multiple_lists_are_ambiguous():
    for answer in ("- 甲公司\n- 甲公司", "- 甲公司\n\n另一组\n- 乙公司", "|甲公司和乙公司|"):
        refs = references_from_committed_result("run-1", response(answer))
        assert resolve_reference("第一只", refs)["status"] == "clarify"


def test_latest_committed_run_wins_and_foreign_page_stock_clarifies():
    older = references_from_committed_result("run-1", response())
    newer = references_from_committed_result("run-2", response("- 甲公司\n- 乙公司", run_id="run-2"))
    assert resolve_reference("第二只", older + newer)["symbols"] == ["000002"]
    assert resolve_reference("第二只", newer, {"symbol": "600001"})["status"] == "clarify"


def test_explicit_batch_keeps_filters_but_no_ordinal_outside_list():
    refs = references_from_committed_result("run-1", response())
    assert resolve_reference("刚才那批走势", refs)["symbols"] == ["000002", "600001"]
    assert resolve_reference("第三只", refs)["status"] == "clarify"
    assert resolve_reference("今天市场如何", refs) == {"status": "none"}
    assert resolve_reference("第二只", [])["status"] == "clarify"


def test_distinct_result_groups_require_clarification():
    refs = references_from_committed_result("run-1", response())
    other = {**refs[0], "reference_id": "other", "ordered_symbols": ["600003"]}
    assert resolve_reference("第二只", [*refs, other])["status"] == "clarify"


def test_source_is_not_mutated_and_reference_has_content_identity():
    original = response()
    refs = references_from_committed_result("run-1", original)
    refs[0]["ordered_symbols"].reverse()
    assert original["tool_results"][0]["output"]["rows"][0]["symbol"] == "600001"
    assert len(refs[0]["result_hash"]) == 64
