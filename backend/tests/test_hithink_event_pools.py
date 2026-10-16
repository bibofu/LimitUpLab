"""Complete event pools must never silently accept missing or duplicated rows."""

import json
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from app.collectors.hithink_finance_collector import HithinkFinanceCollector, HithinkFinanceError


TRADE_DATE = date(2026, 8, 21)
METHODS = ("collect_all_limit_up_pool", "collect_all_limit_break_pool")


class FakeRunner:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.payloads.pop(0)), "")


def stock(index=0, **overrides):
    symbol = str(600000 + index)
    return {
        "ticker": symbol, "thscode": f"{symbol}.SH", "name": f"样本{index}",
        "continue_day_cnt": 2, "limit_up_reason": "测试原因", "seal_money": 123456,
        "open_times": 3, "turnover": 87654321, "turnover_ratio_pct": 12.34,
        **overrides,
    }


def payload(rows, *, page=1, total=None, **pagination):
    return {
        "ok": True,
        "data": {
            "timestamp": 1,  # Readiness timestamps must not alter the requested date.
            "item": rows,
            "pagination": {"page": page, "size": 200, "total": len(rows) if total is None else total, **pagination},
        },
    }


def collect(method, payloads):
    runner = FakeRunner(payloads)
    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    return getattr(collector, method)(trade_date=TRADE_DATE), runner


@pytest.mark.parametrize("method", METHODS)
def test_all_pages_are_combined_with_explicit_shanghai_date(method):
    result, runner = collect(method, [
        payload([stock(index) for index in range(200)], total=201),
        payload([stock(200)], page=2, total=201),
    ])
    assert (result.page, result.page_size, result.total, len(result.items)) == (1, 200, 201, 201)
    assert result.trade_date == TRADE_DATE
    assert result.items[-1].symbol == "600200"
    assert result.source == "hithink-finance"
    midnight = datetime(2026, 8, 21, tzinfo=timezone(timedelta(hours=8)))
    for page, command in enumerate(runner.commands, 1):
        assert command[command.index("--page") + 1] == str(page)
        assert command[command.index("--date-ms") + 1] == str(int(midnight.timestamp() * 1000))
        assert command[command.index("--source") + 1] == "remote"
    if method == METHODS[0]:
        assert result.items[0].board_height == 2
        assert result.items[0].limit_up_reason == "测试原因"
        assert result.items[0].seal_amount == 123456
    else:
        assert result.items[0].break_count == 3
        assert result.items[0].amount == 87654321
        assert result.items[0].turnover_rate == 12.34


@pytest.mark.parametrize("method", METHODS)
def test_explicit_empty_pool_is_valid(method):
    result, runner = collect(method, [payload([])])
    assert result.total == 0 and result.items == []
    assert len(runner.commands) == 1


@pytest.mark.parametrize("pagination", [
    {"total": None}, {"total": 1.5}, {"total": True}, {"total": -1}, {"total": 10001},
    {"page": 2}, {"page": None}, {"size": 100}, {"size": None},
])
@pytest.mark.parametrize("method", METHODS)
def test_invalid_pagination_is_rejected(method, pagination):
    response = payload([stock()])
    response["data"]["pagination"].update(pagination)
    with pytest.raises(HithinkFinanceError, match="pagination"):
        collect(method, [response])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("second_page", [
    payload([], page=2, total=201),  # Premature empty page.
    payload([stock(200)], page=1, total=201),  # Server repeats page one.
    payload([stock(200)], page=2, total=202),  # Pool changed during pagination.
    payload([stock(0)], page=2, total=201),  # Repeated code despite different page number.
])
def test_partial_or_unstable_pool_is_rejected(method, second_page):
    with pytest.raises(HithinkFinanceError):
        collect(method, [payload([stock(index) for index in range(200)], total=201), second_page])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("rows", [
    [None], ["bad row"], [stock(name="")], [stock(name=42)],
    [stock(ticker="invalid", thscode="invalid")], [stock(thscode="600001.SH")],
    [stock(thscode="600000.UNKNOWN")], [stock(), stock()],
    [stock(turnover="invalid")], [stock(turnover=float("inf"))], [stock(seal_money=True)],
])
def test_invalid_records_are_not_silently_dropped(method, rows):
    with pytest.raises(HithinkFinanceError):
        collect(method, [payload(rows)])


@pytest.mark.parametrize("height", [None, "", 0, -1, 1.5, True])
def test_missing_or_invalid_board_height_does_not_become_first_board(height):
    with pytest.raises(HithinkFinanceError, match="height"):
        collect(METHODS[0], [payload([stock(continue_day_cnt=height)])])


@pytest.mark.parametrize("count", [-1, 0.5, True])
def test_invalid_break_count_is_rejected(count):
    with pytest.raises(HithinkFinanceError, match="break count"):
        collect(METHODS[1], [payload([stock(open_times=count)])])


@pytest.mark.parametrize("method", METHODS)
def test_missing_metadata_and_truncation_are_rejected(method):
    for response in ({"ok": True, "data": {"item": []}}, {**payload([stock()]), "meta": {"truncated": True}}):
        with pytest.raises(HithinkFinanceError, match="pagination"):
            collect(method, [response])


def test_single_break_page_keeps_missing_optional_values_missing():
    runner = FakeRunner([payload([stock(open_times=None, turnover=None, turnover_ratio_pct=None)])])
    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    result = collector.collect_limit_break_pool(trade_date=TRADE_DATE, page=1, size=200)
    assert (result.items[0].break_count, result.items[0].amount, result.items[0].turnover_rate) == (None, None, None)
    assert "limit-break-pool" in runner.commands[0]


def test_explicit_wrong_trading_date_is_rejected():
    response = payload([stock()])
    response["data"]["trade_date"] = "2026-08-20"
    with pytest.raises(HithinkFinanceError, match="trade date"):
        collect(METHODS[0], [response])
