"""A missing list row is not evidence of a confirmed non-listing."""

from copy import deepcopy
from datetime import date
from types import SimpleNamespace as Obj
from unittest.mock import Mock

import pytest

from app.agents.review_digest_dragon_tiger import attach_first_board_dragon_tiger
from app.review_digest_models import DigestStock


DAY = date(2026, 9, 28)
OTHER_DAY = date(2026, 9, 29)


def stock(symbol="600001", day=DAY):
    return DigestStock(symbol=symbol, name="样本", first_board_date=day)


def snapshot(symbol="600001", day=DAY, flag=True, source="hithink-finance", reason="日涨幅偏离值", **extra):
    return Obj(trade_date=day, symbol=symbol, dragon_tiger_on_list=flag,
               dragon_tiger_source=source, dragon_tiger_reason=reason, **extra)


def prediction(flag=True, source="eastmoney", **identity):
    return Obj(trade_date=DAY, symbol="600001", facts_json={
        "trade_date": DAY.isoformat(), "symbol": "600001", "enrichment": {
            "dragon_tiger_on_list": flag, "dragon_tiger_source": source,
            "dragon_tiger_reason": "保存时的理由", **identity,
        },
    })


def test_confirmed_prediction_has_priority_and_needs_no_repository_read():
    item, pred = stock(), prediction()
    repo = Mock()
    repo.list_enrichment_for_date.return_value = [snapshot()]
    attach_first_board_dragon_tiger([item], repo, {(DAY, item.symbol): pred})
    assert item.first_dragon_tiger_on_list is True
    assert item.first_dragon_tiger_source == "eastmoney"
    assert item.first_dragon_tiger_reason == "保存时的理由"
    assert item.data_missing == []
    repo.list_enrichment_for_date.assert_not_called()


@pytest.mark.parametrize("source", [None, "", "  ", "unknown", "NULL", "未知", "--", "数据不足"])
def test_true_without_a_valid_source_stays_unknown(source):
    item = stock()
    repo = Mock()
    repo.list_enrichment_for_date.return_value = [snapshot(source=source)]
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is None
    assert item.first_dragon_tiger_source is None
    assert item.first_dragon_tiger_reason == "日涨幅偏离值"
    assert any("缺少有效来源" in text for text in item.data_missing)


@pytest.mark.parametrize("flag,source", [(False, None), (False, "hithink-finance"), (None, "eastmoney"), (1, "eastmoney"), ("true", "eastmoney")])
def test_false_default_or_non_boolean_flag_never_confirms_listing_or_non_listing(flag, source):
    item = stock()
    repo = Mock()
    repo.list_enrichment_for_date.return_value = [snapshot(flag=flag, source=source)]
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is None
    assert item.first_dragon_tiger_source is None
    assert "未保存完整榜单覆盖" in item.data_missing[0]


def test_same_day_snapshot_can_supply_late_positive_without_changing_saved_facts():
    item, pred = stock(), prediction(flag=False, source=None)
    row = snapshot(dragon_tiger_net_buy_amount=12_000)
    before = deepcopy((pred, row))
    repo = Mock()
    repo.list_enrichment_for_date.return_value = [row]
    attach_first_board_dragon_tiger([item], repo, {(DAY, item.symbol): pred})
    assert item.first_dragon_tiger_on_list is True
    assert item.first_dragon_tiger_source == "hithink-finance"
    assert (pred, row) == before
    assert "net_buy" not in " ".join(item.model_dump())


@pytest.mark.parametrize("identity", [{"trade_date": OTHER_DAY.isoformat()}, {"symbol": "600002"}, {"trade_date": "bad-date"}])
def test_contradictory_prediction_enrichment_identity_is_rejected(identity):
    item = stock()
    repo = Mock()
    repo.list_enrichment_for_date.return_value = []
    attach_first_board_dragon_tiger([item], repo, {(DAY, item.symbol): prediction(**identity)})
    assert item.first_dragon_tiger_on_list is None
    assert item.first_dragon_tiger_reason is None


@pytest.mark.parametrize("layer", ["prediction", "facts"])
def test_contradictory_prediction_or_facts_identity_is_rejected(layer):
    item, pred = stock(), prediction()
    if layer == "prediction":
        pred.trade_date = OTHER_DAY
    else:
        pred.facts_json["symbol"] = "600002"
    repo = Mock()
    repo.list_enrichment_for_date.return_value = []
    attach_first_board_dragon_tiger([item], repo, {(DAY, item.symbol): pred})
    assert item.first_dragon_tiger_on_list is None


def test_repository_rows_require_exact_date_and_symbol_and_missing_identity_is_rejected():
    item = stock()
    repo = Mock()
    repo.list_enrichment_for_date.return_value = [
        snapshot(day=OTHER_DAY), snapshot(symbol="600002"), snapshot(day=None),
    ]
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is None
    assert item.first_dragon_tiger_reason is None


def test_one_read_per_needed_date_and_no_read_without_anchor():
    items = [stock(), stock("600002"), stock("600003", OTHER_DAY), stock("600004", None)]
    repo = Mock()
    repo.list_enrichment_for_date.side_effect = lambda day: [snapshot(symbol="600002", day=day)]
    attach_first_board_dragon_tiger(items, repo)
    assert [call.args[0] for call in repo.list_enrichment_for_date.call_args_list] == [DAY, OTHER_DAY]
    assert [item.first_dragon_tiger_on_list for item in items] == [None, True, None, None]
    assert "首板日期未确认" in items[-1].data_missing[0]


def test_read_failure_is_unknown_and_does_not_duplicate_missing_messages():
    item = stock()
    item.data_missing = ["其他特征缺失"]
    repo = Mock()
    repo.list_enrichment_for_date.side_effect = RuntimeError("fixture")
    attach_first_board_dragon_tiger([item], repo)
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is None
    assert len(item.data_missing) == 2 and item.data_missing[0] == "其他特征缺失"
    assert "读取失败" in item.data_missing[1]


def test_late_positive_clears_only_owned_missing_status_and_cannot_leave_stale_fields():
    item = stock()
    item.data_missing = ["其他特征缺失"]
    repo = Mock()
    repo.list_enrichment_for_date.return_value = []
    attach_first_board_dragon_tiger([item], repo)
    repo.list_enrichment_for_date.return_value = [snapshot(source=" hithink-finance ")]
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is True
    assert item.first_dragon_tiger_source == "hithink-finance"
    assert item.data_missing == ["其他特征缺失"]
    item.first_board_date = OTHER_DAY
    attach_first_board_dragon_tiger([item], repo)
    assert item.first_dragon_tiger_on_list is None
    assert item.first_dragon_tiger_source is None and item.first_dragon_tiger_reason is None
