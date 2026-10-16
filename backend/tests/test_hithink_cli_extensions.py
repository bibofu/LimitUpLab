from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector, HithinkFinanceError, HithinkIndexCatalogFact,
)


SHANGHAI = timezone(timedelta(hours=8))
DAY = date(2026, 10, 15)


def ms(day):
    return int(datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).timestamp() * 1000)


def envelope(rows, **data):
    return {"ok": True, "data": {"item": rows, **data}}


def collector(*payloads):
    instance = HithinkFinanceCollector(executable="unused-test-cli")
    instance._invoke = Mock(side_effect=payloads)
    return instance


def bar(day=DAY):
    return {
        "date_ms": ms(day), "open_price": 100, "high_price": 103, "low_price": 99,
        "close_price": 102, "volume": 500_000, "turnover": 50_000_000,
    }


@pytest.mark.parametrize("truncated", [False, True])
def test_index_snapshot_rejects_partial_envelope_even_when_requested_code_is_present(truncated):
    payload = envelope([{"thscode": "880001.TI", "price_change_ratio_pct": 2.5}], count=2)
    if truncated:
        payload["data"]["count"] = 1
        payload["meta"] = {"truncated": True}
    with pytest.raises(HithinkFinanceError, match="incomplete"):
        collector(payload).collect_index_snapshots([
            HithinkIndexCatalogFact("880001.TI", "半导体", "industry"),
        ])


def pool_row(i):
    symbol = str(600000 + i)
    return {"thscode": f"{symbol}.SH", "ticker": symbol, "name": f"股票{i}", "price_change_ratio_pct": -10}


def pool_page(page, rows, total=201):
    return envelope(rows, trade_date=DAY.isoformat(), pagination={"page": page, "size": 200, "total": total})


def test_index_history_uses_shanghai_inclusive_dates_and_no_stock_adjustment():
    instance = collector(envelope([bar(DAY + timedelta(days=1)), bar(), bar(DAY - timedelta(days=1))], count=3))
    history = instance.collect_index_history(" 880001.ti ", start_date=DAY, end_date=DAY)
    assert history.thscode == "880001.TI"
    assert [item.trade_date for item in history.items] == [DAY]
    assert history.items[0].close == 102
    assert history.items[0].volume == 500_000
    assert history.items[0].turnover == 50_000_000
    assert history.adjustment == "none"
    instance._invoke.assert_called_once_with(
        "index", "history", "--thscode", "880001.TI",
        "--start-ms", str(ms(DAY)), "--end-ms", str(ms(DAY + timedelta(days=1))),
    )


def test_index_history_sorts_returned_trading_days():
    previous = DAY - timedelta(days=1)
    instance = collector(envelope([bar(), bar(previous)]))
    history = instance.collect_index_history("880001.TI", start_date=previous, end_date=DAY)
    assert [item.trade_date for item in history.items] == [previous, DAY]


@pytest.mark.parametrize("field", ["open_price", "high_price", "low_price", "close_price", "volume", "turnover", "date_ms"])
def test_index_history_rejects_missing_required_fields(field):
    row = bar()
    del row[field]
    instance = collector(envelope([row]))
    with pytest.raises(HithinkFinanceError):
        instance.collect_index_history("880001.TI", start_date=DAY, end_date=DAY)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "unavailable"])
def test_index_history_rejects_nonfinite_or_invalid_prices(value):
    row = {**bar(), "close_price": value}
    instance = collector(envelope([row]))
    with pytest.raises(HithinkFinanceError):
        instance.collect_index_history("880001.TI", start_date=DAY, end_date=DAY)


@pytest.mark.parametrize("payload", [
    {**envelope([bar()]), "meta": {"truncated": True}},
    envelope([bar()], count=2),
    envelope([bar(), bar()]),
])
def test_index_history_rejects_truncation_count_mismatch_or_duplicate_days(payload):
    instance = collector(payload)
    with pytest.raises(HithinkFinanceError):
        instance.collect_index_history("880001.TI", start_date=DAY, end_date=DAY)


def test_index_history_reversed_interval_does_not_query_cli():
    instance = collector()
    with pytest.raises(ValueError):
        instance.collect_index_history("880001.TI", start_date=DAY, end_date=DAY - timedelta(days=1))
    instance._invoke.assert_not_called()


def test_calendar_accepts_consistent_date_fields_and_sorts():
    previous = DAY - timedelta(days=1)
    instance = collector(envelope([
        {"date_ms": ms(DAY), "date": DAY.isoformat()},
        {"date_ms": ms(previous)},
    ], count=2))
    assert instance.collect_trading_calendar() == [previous, DAY]
    instance._invoke.assert_called_once_with("market", "calendar")


@pytest.mark.parametrize("row", [
    {"date_ms": ms(DAY), "date": "2026-10-14"},
    {"date_ms": ms(DAY), "date": "invalid"},
    {"date": DAY.isoformat()},
    {"date_ms": "invalid"},
])
def test_calendar_rejects_conflicting_or_invalid_dates(row):
    instance = collector(envelope([row]))
    with pytest.raises(HithinkFinanceError):
        instance.collect_trading_calendar()


@pytest.mark.parametrize("payload", [
    envelope([]),
    envelope([{"date_ms": ms(DAY)}, {"date_ms": ms(DAY)}]),
    envelope([{"date_ms": ms(DAY)}], count=2),
    {**envelope([{"date_ms": ms(DAY)}]), "meta": {"truncated": True}},
])
def test_calendar_rejects_empty_duplicate_or_incomplete_responses(payload):
    with pytest.raises(HithinkFinanceError):
        collector(payload).collect_trading_calendar()


def test_catalog_preserves_full_directory_and_category():
    instance = collector(envelope([
        {"thscode": "880001.TI", "name": " 半导体 "},
        {"thscode": "880002.TI", "name": "汽车"},
    ], count=2))
    result = instance.collect_index_catalog("industry")
    assert [(item.thscode, item.name, item.category) for item in result] == [
        ("880001.TI", "半导体", "industry"), ("880002.TI", "汽车", "industry"),
    ]


@pytest.mark.parametrize("payload", [
    envelope([{"thscode": "880001.TI", "name": "半导体"}], count=2),
    {**envelope([{"thscode": "880001.TI", "name": "半导体"}]), "meta": {"truncated": True}},
    envelope([{"thscode": "880001.TI"}]),
    envelope([{"thscode": "880001.TI", "name": "半导体"}] * 2),
])
def test_catalog_does_not_silently_drop_truncated_or_invalid_indexes(payload):
    with pytest.raises(HithinkFinanceError):
        collector(payload).collect_index_catalog("industry")


def test_limit_down_pool_collects_all_pages_with_explicit_date_and_sort():
    instance = collector(pool_page(1, [pool_row(i) for i in range(200)]), pool_page(2, [pool_row(200)]))
    result = instance.collect_all_limit_down_pool(trade_date=DAY)
    assert len(result.items) == 201
    assert result.items[-1].symbol == "600200"
    assert result.items[-1].change_pct == -10
    for page, call in enumerate(instance._invoke.call_args_list, 1):
        assert call.args == (
            "special", "limit-down-pool", "--date-ms", str(ms(DAY)), "--page", str(page),
            "--size", "200", "--sort-field", "last_limit_time", "--sort-dir", "asc",
        )


@pytest.mark.parametrize("second", [
    HithinkFinanceError("page unavailable"),
    pool_page(2, []),
    pool_page(2, [pool_row(0)]),
    pool_page(2, [pool_row(200)], total=202),
    {**pool_page(2, [pool_row(200)]), "meta": {"truncated": True}},
])
def test_limit_down_second_page_failure_never_returns_first_page_as_complete(second):
    instance = collector(pool_page(1, [pool_row(i) for i in range(200)]), second)
    with pytest.raises(HithinkFinanceError):
        instance.collect_all_limit_down_pool(trade_date=DAY)
    assert instance._invoke.call_count == 2


def test_empty_limit_down_pool_is_valid_when_pagination_reports_zero():
    result = collector(pool_page(1, [], total=0)).collect_all_limit_down_pool(trade_date=DAY)
    assert result.items == []


def test_market_snapshots_preserve_all_codes_over_one_hundred_and_deduplicate_input():
    rows = [{"thscode": f"{600000 + i}.SH", "ticker": str(600000 + i), "last_price": 10} for i in range(205)]
    payloads = [envelope(rows[offset : offset + 100], timestamp=ms(DAY)) for offset in range(0, len(rows), 100)]
    instance = collector(*payloads)
    codes = [row["thscode"] for row in rows]
    result = instance.collect_market_snapshots([" ", codes[0], *codes, ""])
    assert len(result.items) == result.total == 205
    assert [item.thscode for item in result.items] == codes
    assert instance._invoke.call_count == 3
    for offset, call in zip(range(0, len(rows), 100), instance._invoke.call_args_list):
        expected = codes[offset : offset + 100]
        assert call.args == ("market", "snapshot", "--thscodes", ",".join(expected), "--limit", str(len(expected)))


def test_market_snapshot_batches_cannot_mix_trading_dates():
    codes = [f"{600000 + i}.SH" for i in range(101)]
    instance = collector(
        envelope([{"thscode": code} for code in codes[:100]], timestamp=ms(DAY)),
        envelope([{"thscode": codes[-1]}], timestamp=ms(DAY + timedelta(days=1))),
    )
    with pytest.raises(HithinkFinanceError, match="different dates"):
        instance.collect_market_snapshots(codes)
