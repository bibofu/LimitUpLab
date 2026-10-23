import pytest

from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery


def evidence():
    store = EvidenceStore()
    key = store.add(tool="dragon_tiger_list", state="ok", arguments={"trade_date": "2026-10-21"},
        payload={"trade_date": "2026-10-21", "items": [
            {"symbol": "000001", "name": "样本", "listing_reason": None,
             "limit_reason": "来源题材", "net_buy_amount": 123.5, "trade_date": "2026-10-21"}]})
    return store, key


def table(key, **extra):
    return {"evidence_id": key, "columns": [{"field": "symbol", "label": "代码"},
            {"field": "name", "label": "名称"}, {"field": "listing_reason", "label": "上榜原因"},
            {"field": "net_buy_amount", "label": "净买入金额"}], **extra}


def task():
    return TaskInterpretation.model_validate({"output_contract": {"mode": "table_only",
        "table_required": True, "fields": ["symbol", "name", "listing_reason", "net_buy_amount"],
        "required_metadata": ["date", "units"]}})


def test_typed_table_preserves_source_nulls_and_requested_date_and_units_without_model_prose():
    store, key = evidence()
    final = Finish(status="partial", missing=["来源未提供披露上榜原因"], delivery="table",
                   table=table(key, metadata=["date", "units"]))
    validate_delivery(final, task(), store)
    answer = render_answer(final, store, task().output_contract)
    assert "数据日期：2026-10-21" in answer
    assert "净买入金额（元）" in answer
    assert "数据未成功获取" in answer and "来源题材" not in answer
    assert final.answer == ""


def test_requested_source_reason_null_is_partial_without_affecting_unrequested_optional_fields():
    store, key = evidence()
    final = Finish(status="complete", delivery="table", table=table(key, metadata=["date", "units"]))
    with pytest.raises(ValueError, match="source-specific field is unavailable"):
        validate_delivery(final, task(), store)
    final.table.columns = final.table.columns[:2]
    interpretation = TaskInterpretation.model_validate({"output_contract": {
        "mode": "table_only", "fields": ["symbol", "name"], "required_metadata": ["date"]}})
    validate_delivery(final, interpretation, store)


@pytest.mark.parametrize("answer", ["补充说明", "{{evidence_table}}", " \n "])
def test_typed_table_rejects_original_text_instead_of_silently_deleting_it(answer):
    _, key = evidence()
    with pytest.raises(ValueError, match="prose is never discarded"):
        Finish(status="complete", delivery="table", table=table(key), answer=answer)


def test_finish_remains_single_table_and_requires_structured_selection():
    _, key = evidence()
    with pytest.raises(ValueError):
        Finish(status="complete", delivery="table", table=[table(key), table(key)])
    with pytest.raises(ValueError, match="requires table"):
        Finish(status="complete", delivery="table")


def test_validation_reports_field_format_and_metadata_errors_together():
    store, key = evidence()
    wrong = table(key)
    wrong["columns"][2]["field"] = "limit_up_reason"
    final = Finish(status="complete", answer="错误说明\n\n{{evidence_table}}", table=wrong)
    with pytest.raises(ValueError) as caught:
        validate_delivery(final, task(), store)
    message = str(caught.value)
    assert "requested order" in message and "without heading or prose" in message
    assert "date/unit annotation" in message and "not defined for this evidence source" in message


def test_metadata_never_copies_requested_date_when_source_date_is_missing():
    store, key = evidence()
    record = store.get(key)
    record["payload"].pop("trade_date")
    record["rows"][0].pop("trade_date")
    final = Finish(status="complete", delivery="table", table=table(key, metadata=["date"]))
    with pytest.raises(ValueError, match="date annotation is unavailable"):
        render_answer(final, store)


def test_units_remain_declared_when_money_values_are_null():
    store, key = evidence()
    store.get(key)["rows"][0]["net_buy_amount"] = None
    final = Finish(status="complete", delivery="table", table=table(key, metadata=["units"]))
    assert "净买入金额（元）" in render_answer(final, store)
