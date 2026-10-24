"""Model-facing field definitions are sent once per tool and run."""

from __future__ import annotations

from app.agents.runner.definitions import DefinitionDeduplicator


def payload(definitions, **extra):
    return {
        "name": "index_quote",
        "status": "ok",
        "rows": [{"close": 1}],
        "field_definitions": definitions,
        **extra,
    }


def test_first_result_keeps_full_definitions():
    dedup = DefinitionDeduplicator()
    value = payload({"rows": {"close": "收盘点位"}})

    view = dedup.compact("index_quote", value)

    assert view["field_definitions"] == {"rows": {"close": "收盘点位"}}
    assert "field_definitions_ref" not in view
    assert dedup.saved_chars == 0 and dedup.compacted_results == 0


def test_repeated_definitions_are_replaced_with_a_reference():
    dedup = DefinitionDeduplicator()
    first = payload({"rows": {"close": "收盘点位", "open": "开盘点位"}})
    second = payload({"rows": {"close": "收盘点位", "open": "开盘点位"}})

    dedup.compact("index_quote", first)
    view = dedup.compact("index_quote", second)

    assert "field_definitions" not in view
    assert "index_quote" in view["field_definitions_ref"]
    assert "2 个字段" in view["field_definitions_ref"]
    assert dedup.compacted_results == 1
    assert dedup.saved_chars > 0
    assert view["rows"] == second["rows"]


def test_only_new_fields_are_sent_with_a_scope_note():
    dedup = DefinitionDeduplicator()
    dedup.compact("index_quote", payload({"rows": {"close": "收盘点位"}}))

    view = dedup.compact("index_quote", payload({
        "rows": {"close": "收盘点位"},
        "metadata": {"data_as_of": "数据截止日"},
    }))

    assert view["field_definitions"] == {"metadata": {"data_as_of": "数据截止日"}}
    assert view["field_definitions_scope"] == "仅列出此前未发送的字段"
    assert "1 个字段" in view["field_definitions_ref"]


def test_changed_description_is_sent_again():
    dedup = DefinitionDeduplicator()
    dedup.compact("index_quote", payload({"rows": {"close": "旧说明"}}))

    view = dedup.compact("index_quote", payload({"rows": {"close": "新说明"}}))

    assert view["field_definitions"] == {"rows": {"close": "新说明"}}
    assert "field_definitions_ref" not in view


def test_tools_are_tracked_independently():
    dedup = DefinitionDeduplicator()
    dedup.compact("index_quote", payload({"rows": {"close": "收盘点位"}}))

    view = dedup.compact("stock_kline", payload({"rows": {"close": "收盘价"}}))

    assert view["field_definitions"] == {"rows": {"close": "收盘价"}}
    assert "field_definitions_ref" not in view


def test_values_without_definitions_are_unchanged():
    dedup = DefinitionDeduplicator()
    value = {"name": "market_summary", "status": "error", "error": "x"}

    assert dedup.compact("market_summary", value) == value
    assert dedup.compacted_results == 0


def test_input_value_is_not_mutated():
    dedup = DefinitionDeduplicator()
    first = payload({"rows": {"close": "收盘点位"}})
    dedup.compact("index_quote", first)

    second = payload({"rows": {"close": "收盘点位"}})
    dedup.compact("index_quote", second)

    assert second["field_definitions"] == {"rows": {"close": "收盘点位"}}
