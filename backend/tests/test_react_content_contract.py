"""Content obligations and table columns are distinct; these are protocol fixtures."""

import pytest

from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from test_react_completion_integration import DATE, Script, execute, native, reply


def unknown_column_task(**options):
    return TaskInterpretation(current_task="列代码与不存在的研究指标", output_contract={
        "mode": "freeform", "table_required": True,
        "field_requests": [{"requested": "代码", "field": "symbol"},
                           {"requested": "不存在的研究指标", "field": None}], **options})


@pytest.mark.parametrize("table_required", [False, True])
def test_unknown_explicit_column_cannot_complete_by_omitting_the_table(table_required):
    task = unknown_column_task(table_required=table_required)
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(Finish(status="complete", answer="已经完成。"), task)


def test_partial_can_deliver_known_columns_with_the_unavailable_column_explained():
    task = unknown_column_task()
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": [{"symbol": "600001"}]}, state="ok", arguments={})
    final = Finish(status="partial", answer="{{evidence_table}}\n\n研究指标未取得，无法补充。",
                   missing=["用户要求的研究指标未取得"], evidence_ids=[key], table={
                       "evidence_id": key, "columns": [{"field": "symbol", "label": "代码"}]})
    validate_delivery(final, task, store)
    assert "600001" in render_answer(final, store)
    assert task.output_contract.field_requests[-1].field is None


def test_valid_empty_list_does_not_require_fabricated_row_values():
    task = unknown_column_task()
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": []}, state="empty", arguments={})
    final = Finish(status="empty", answer="本轮查询没有匹配记录。", evidence_ids=[key])
    validate_delivery(final, task, store)
    assert render_answer(final, store) == final.answer


def test_unknown_legacy_columns_survive_repeated_checkpoint_round_trips():
    task = TaskInterpretation.model_validate({"current_task": "只列代码与未知指标。", "output_contract": {
        "mode": "table_only", "table_required": True, "fields": ["symbol", "unknown_metric"]}})
    for _ in range(2):
        task = TaskInterpretation.model_validate(task.model_dump(mode="json"))
        assert task.output_contract.field_resolution.status == "unresolved"
        assert task.output_contract.field_requests[-1].field == "unknown_metric"
        with pytest.raises(ValueError, match="fields remain unresolved"):
            validate_delivery(Finish(status="complete", answer="完成。"), task)


def test_model_diagnostic_cannot_convert_an_unknown_column_to_a_resolved_one():
    task = unknown_column_task(field_resolution={
        "status": "resolved", "proposed_fields": ["symbol"], "reason": "claimed by model"})
    assert task.output_contract.field_resolution.status == "unresolved"
    assert task.output_contract.field_resolution.proposed_fields == ["symbol", None]


def test_known_catalog_column_still_requires_the_field_in_actual_evidence_rows():
    task = TaskInterpretation(output_contract={"mode": "table_only", "table_required": True,
                                               "fields": ["symbol", "amount"]})
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": [{"symbol": "600001"}]}, state="ok", arguments={})
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[key], table={
        "evidence_id": key, "columns": [{"field": "symbol", "label": "代码"},
                                        {"field": "amount", "label": "成交额"}]})
    validate_delivery(final, task, store)
    with pytest.raises(ValueError, match="Table field missing"):
        render_answer(final, store)


def test_partial_does_not_disable_exact_bound_column_order():
    task = TaskInterpretation(output_contract={"fields": ["symbol", "name"]})
    final = Finish(status="partial", answer="{{evidence_table}}", missing=["另一个请求未完成"], table={
        "evidence_id": "fixture", "columns": [{"field": "name", "label": "名称"},
                                               {"field": "symbol", "label": "代码"}]})
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(final, task)


def test_multiple_content_metrics_are_preserved_without_becoming_table_columns():
    requested = ["涨停总数", "首板数量", "最高连板高度"]
    task = TaskInterpretation(current_task="给出三个市场指标，不用表格。", output_contract={
        "mode": "prose_only", "content_requirements": requested})
    assert task.output_contract.content_requirements == requested
    assert task.output_contract.fields == task.output_contract.field_requests == []
    assert task.output_contract.field_resolution.status == "unrestricted"
    assert task.pending_slots == []


def test_unknown_content_is_retained_for_research_instead_of_forced_into_catalog():
    requested = "解释用户定义但尚未提供口径的能量指标"
    task = TaskInterpretation(current_task=requested, output_contract={
        "mode": "prose_only", "content_requirements": [requested]})
    assert task.output_contract.content_requirements == [requested]
    assert task.output_contract.field_resolution.status == "unrestricted"
    # This tests truthful gap delivery, not whether a model detects every gap.
    final = Finish(status="partial", answer="已取得记录，但能量指标没有定义，暂无法计算。",
                   missing=["能量指标的定义和计算口径未确定"])
    validate_delivery(final, task)
    assert task.output_contract.content_requirements == [requested]


def test_content_requirements_do_not_disable_an_unknown_explicit_column():
    task = unknown_column_task(content_requirements=["给出合计数量"])
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(Finish(status="complete", answer="合计1只。"), task)


def test_content_and_column_requirements_survive_checkpoint_without_cross_conversion():
    task = TaskInterpretation(output_contract={"mode": "freeform", "table_required": True,
        "fields": ["symbol", "name"], "content_requirements": ["另给出总数量及统计日期"]})
    for _ in range(2):
        task = TaskInterpretation.model_validate(task.model_dump(mode="json"))
        assert task.output_contract.fields == ["symbol", "name"]
        assert [item.field for item in task.output_contract.field_requests] == ["symbol", "name"]
        assert task.output_contract.content_requirements == ["另给出总数量及统计日期"]
    legacy = TaskInterpretation(output_contract={"fields": ["symbol", "name"]})
    assert legacy.output_contract.content_requirements == []
    assert legacy.output_contract.fields == ["symbol", "name"]


def reviewed_contract(response):
    return next(trace.output["output_contract"] for trace in response.tool_results
                if trace.name == "react_task_context")


def test_full_graph_publishes_multiple_prose_metrics_without_column_contract_failure():
    class Model(Script):
        output_contract = {"mode": "prose_only", "table_required": False,
                           "content_requirements": ["涨停数量", "成交额合计"]}

        def decide(self, messages, results):
            if self.rounds == 1:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"))
            source = results["query"]["evidence_id"]
            if self.rounds == 2:
                return reply(native("compute_result", {"evidence_id": source, "operation": "aggregate",
                    "aggregate": "sum", "metric": "amount"}, "sum"))
            total = results["sum"]
            assert total["rows"][0]["value"] == 210
            return reply(native("finish", {"status": "complete", "answer": "查询日涨停6只，成交额合计210元。",
                "evidence_ids": [source, total["evidence_id"]], "missing": []}, "finish"))

    model = Model()
    response, queries = execute(model, message=f"{DATE}的涨停数量和成交额合计是多少？不用表格。")
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert model.rounds == 3 and len(queries) == 1
    assert "6只" in response.answer and "210元" in response.answer
    contract = reviewed_contract(response)
    assert contract["fields"] == [] and contract["content_requirements"] == ["涨停数量", "成交额合计"]
    assert all(trace.output["passed"] for trace in response.tool_results if trace.name == "react_answer_check")


def test_full_graph_publishes_evidence_table_and_total_as_separate_deliverables():
    class Model(Script):
        output_contract = {"mode": "freeform", "table_required": True,
            "content_requirements": ["表格外给出涨停总数量"],
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "名称", "field": "name"}]}

        def decide(self, messages, results):
            if self.rounds == 1:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"))
            source = results["query"]["evidence_id"]
            return reply(native("finish", {"status": "complete", "answer": "{{evidence_table}}\n\n合计6只。",
                "table": {"evidence_id": source, "columns": [{"field": "symbol", "label": "代码"},
                                                               {"field": "name", "label": "名称"}]},
                "evidence_ids": [source], "missing": []}, "finish"))

    model = Model()
    response, queries = execute(model, message=f"列出{DATE}涨停名单，表内只列代码名称，并在表外给出总数量。")
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert model.rounds == 2 and len(queries) == 1
    assert response.answer.startswith("| 代码 | 名称 |")
    assert response.answer.count("| 60010") == 6 and response.answer.endswith("合计6只。")
    contract = reviewed_contract(response)
    assert contract["fields"] == ["symbol", "name"]
    assert contract["content_requirements"] == ["表格外给出涨停总数量"]
