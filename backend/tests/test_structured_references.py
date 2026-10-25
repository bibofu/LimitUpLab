import pytest

from copy import deepcopy

from app.agents.structured_references import (
    constraints_from_references, references_from_committed_result, resolve_reference, supported_session_constraints,
)


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


@pytest.mark.parametrize("answer", [
    "- 甲公司\n- 丙公司\n- 乙公司",
    "| 股票 | 代码 |\n|---|---|\n|甲公司|600001|\n|丙公司|600003|\n|乙公司|000002|",
])
def test_unknown_middle_row_cannot_shift_displayed_ordinals(answer):
    refs = references_from_committed_result("run-1", response(answer))
    assert resolve_reference("第二只", refs)["status"] == "clarify"


def scoped_response(**arguments):
    result = response()
    result["tool_results"][0]["output"]["query_scope"] = {
        "scope_version": 1, "tool": "ratings",
        "arguments": {"trade_date": "2026-10-23", "board_height": 1, "market": "main",
                      "event_status": "closed_limit", "sort_by": "symbol", "sort_order": "asc", **arguments},
        "explicit_arguments": {"board_height": 1, "market": "main", **arguments},
    }
    return result


def test_conditions_and_sorting_come_from_the_committed_invocation_scope():
    source = scoped_response()
    source["tool_results"][0]["output"]["filters"] = {"market": "fabricated-output-filter"}
    refs = references_from_committed_result("run-1", source)
    resolved = resolve_reference("继续按上次条件", refs)
    assert resolved["status"] == "resolved" and resolved["query_scope_status"] == "known"
    assert resolved["filters"] == {"board_height": 1, "market": "main", "event_status": "closed_limit"}
    assert resolved["sort_rules"] == {"sort_by": "symbol", "sort_order": "asc"}
    assert resolved["query_scope"]["arguments"]["trade_date"] == "2026-10-23"


def test_legacy_output_filters_cannot_prove_conditions_but_ordinals_still_work():
    source = response()
    source["tool_results"][0]["output"]["filters"] = {"board_height": 1}
    refs = references_from_committed_result("run-1", source)
    assert refs[0]["query_scope_status"] == "unknown" and refs[0]["filters"] is None
    assert resolve_reference("第二只", refs)["symbols"] == ["600001"]
    assert resolve_reference("继续按上次条件", refs)["status"] == "clarify"
    assert constraints_from_references(refs) == []


@pytest.mark.parametrize("change", [{"scope_version": 2}, {"tool": "another_tool"},
                                    {"arguments": None}, {"explicit_arguments": []}])
def test_malformed_or_foreign_invocation_scope_cannot_authorize_inheritance(change):
    source = scoped_response()
    source["tool_results"][0]["output"]["query_scope"].update(change)
    refs = references_from_committed_result("run-1", source)
    assert resolve_reference("继续按上次条件", refs)["status"] == "clarify"
    assert constraints_from_references(refs) == []


def test_identical_lists_with_different_conditions_require_condition_clarification():
    source = scoped_response()
    source["tool_results"].append(scoped_response(market="growth")["tool_results"][0])
    refs = references_from_committed_result("run-1", source)
    assert resolve_reference("继续按上次条件", refs)["status"] == "clarify"
    assert resolve_reference("第二只", refs)["symbols"] == ["600001"]


def test_equal_effective_conditions_do_not_conflict_when_explicit_keys_differ():
    source = scoped_response()
    duplicate = deepcopy(source["tool_results"][0])
    duplicate["output"]["query_scope"]["explicit_arguments"] = {"board_height": 1}
    source["tool_results"].append(duplicate)
    refs = references_from_committed_result("run-1", source)
    assert resolve_reference("继续按上次条件", refs)["status"] == "resolved"


def test_paged_receipt_view_never_creates_an_extra_source_reference():
    source = scoped_response()
    page = deepcopy(source["tool_results"][0])
    page["name"] = "read_tool_result"
    source["tool_results"].append(page)
    refs = references_from_committed_result("run-1", source)
    assert len(refs) == 1 and refs[0]["source_tool"] == "ratings"


def test_typed_conditions_keep_source_scope_and_do_not_alias_original_values():
    source = scoped_response(symbols=["600001", "000002"])
    refs = references_from_committed_result("run-1", source)
    constraints = constraints_from_references(refs)
    market = next(item for item in constraints if item["key"] == "market")
    assert market == {"scope": "session", "key": "market", "value": "main", "tool": "ratings",
        "source_run_id": "run-1", "source_message_id": "user_run-1", "reference_id": refs[0]["reference_id"],
        "origin": "committed_tool_scope", "application": "reference_followup"}
    symbols = next(item for item in constraints if item["key"] == "symbols")
    symbols["value"].reverse()
    assert source["tool_results"][0]["output"]["query_scope"]["arguments"]["symbols"] == ["600001", "000002"]
    assert supported_session_constraints([market, {**market, "value": "growth"}], refs) == [market]
