import copy
from datetime import date, timedelta
import json

import pytest

from app.agents.context_builder import estimate_tokens
from app.agents.tool_context import FORMAT_KEY, ToolContextError, expand_tool_observation, format_tool_observation


def stock_list():
    return {"name": "first_board_ratings", "status": "ok", "row_count": 68,
            "trade_date": "2026-10-23", "source": "fixed-tool-world", "truncated": False,
            "field_definitions": {"rows": {"symbol": "六位A股代码", "score": "研究评分，不是收益预测"}},
            "rows": [{"symbol": f"{600001 + index:06}", "name": f"样本股票{index + 1}",
                      "score": 99 - index / 2, "turnover_rate": index / 3,
                      "limit_up_reason": "固定世界测试事实", "data_missing": [],
                      "trade_date": "2026-10-23", "source": "fixed-tool-world"} for index in range(68)]}


def klines():
    return {"name": "stock_kline", "status": "ok", "row_count": 60, "symbol": "600001",
            "data_as_of": "2026-10-23", "calendar_coverage_status": "unverified",
            "field_definitions": {"rows": {"close": "未复权收盘价，元", "volume": "来源单位未核验"}},
            "data_missing": ["calendar_coverage"], "rows": [
                {"trade_date": (date(2026, 7, 1) + timedelta(days=index)).isoformat(),
                 "open": 10 + index / 10, "high": 11 + index / 10, "low": 9 + index / 10,
                 "close": 10.5 + index / 10, "volume": 10000 + index,
                 "change_pct": None if index % 10 == 0 else 0.5,
                 "source": "fixed-tool-world", "price_basis": "unadjusted"} for index in range(60)]}


@pytest.mark.parametrize("factory,count", [(stock_list, 68), (klines, 60)])
def test_complete_rows_roundtrip_without_fact_or_order_loss(factory, count):
    original = factory()
    snapshot = copy.deepcopy(original)
    view = format_tool_observation(original)
    assert FORMAT_KEY in view
    assert len(view["rows_table"]["values"]) == count
    assert expand_tool_observation(view) == snapshot
    assert original == snapshot
    assert view["field_definitions"] == original["field_definitions"]
    assert view["row_count"] == count
    assert estimate_tokens(view) < estimate_tokens(original)


def test_absent_fields_and_explicit_null_are_distinct_after_json_roundtrip():
    original = stock_list()
    original["rows"][0]["optional_value"] = None
    original["rows"][2]["optional_value"] = {"__missing__": True}
    view = json.loads(json.dumps(format_tool_observation(original), ensure_ascii=False))
    assert FORMAT_KEY in view
    expanded = expand_tool_observation(view)
    assert expanded == original
    assert expanded["rows"][0]["optional_value"] is None
    assert "optional_value" not in expanded["rows"][1]
    assert expanded["rows"][2]["optional_value"] == {"__missing__": True}


def test_formatting_is_independent_of_prior_calls_and_recovery_process():
    original = stock_list()
    first = format_tool_observation(original)
    restored_receipt = json.loads(json.dumps(original, ensure_ascii=False))
    assert format_tool_observation(restored_receipt) == first
    assert format_tool_observation(original) == first


@pytest.mark.parametrize("value", [{"rows": []}, {"rows": [{"symbol": "600001"}]},
                                  {"status": "error", "error": "provider failed"},
                                  {"rows": [{"a": 1}, {"a": 2}]}])
def test_small_error_and_empty_results_remain_unchanged(value):
    assert format_tool_observation(value) == value


def test_reserved_keys_are_never_overwritten():
    original = {**stock_list(), "rows_table": {"source_fact": 123}}
    assert format_tool_observation(original) == original


def test_deduplicated_definitions_are_not_mistaken_for_a_complete_receipt():
    with pytest.raises(ToolContextError):
        format_tool_observation({**stock_list(), "field_definitions_ref": "earlier result"})


def test_modified_compact_view_cannot_mutate_public_evidence():
    original = stock_list()
    view = format_tool_observation(original)
    view["rows_table"]["values"][0][0] = "000000"
    assert original["rows"][0]["symbol"] == "600001"
