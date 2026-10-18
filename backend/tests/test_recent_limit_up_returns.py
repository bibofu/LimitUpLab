"""Complete local candidate scans and auditable trading-session return windows."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.react_runtime.contracts import Compute, EvidenceColumn, EvidenceTable, Filter, Finish
from app.agents.react_runtime.date_scope import DateScope
from app.agents.react_runtime.evidence import EvidenceStore, TOOL_COLLECTIONS
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import OutputContract
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry, V1_AGENT_PROFILE
from app.repositories.post_limit_repository import PostLimitDataset
from app.services.recent_limit_up_returns import build_recent_limit_up_returns


def dataset(count=2):
    days, day = [], date(2026, 7, 1)
    while len(days) < 10:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    events = [{"symbol": "999999", "name": "未封板", "trade_date": day,
               "closed_limit": 0} for day in days]
    bars = []
    for index in range(count):
        symbol = str(600001 + index)
        events.append({"symbol": symbol, "name": f"样本{index}",
                       "trade_date": days[-1], "closed_limit": 1})
        bars.extend({"symbol": symbol, "trade_date": day,
                     "close": 13.0 if day == days[-1] else 10.0,
                     "source": "hithink-finance.market.history.none"} for day in days)
    return PostLimitDataset(events, bars, days, days, date.fromisoformat(days[-1]))


def registry(monkeypatch, data):
    monkeypatch.setattr("app.agents.tools.load_post_limit_dataset", lambda *a, **kw: data)
    return AgentToolRegistry([], first_board_repository=SimpleNamespace(database_path=Path("unused.sqlite")),
                             hithink_collector=object(), profile=V1_AGENT_PROFILE)


def test_five_sessions_require_six_closes_and_deduplicate_candidate_events():
    data = dataset(1)
    for bar in data.bars:
        if bar["trade_date"] == data.calendar[-5]:
            bar["close"] = 12.0
    event = {**data.events[-1], "trade_date": data.calendar[-2]}
    data.events.extend([event, dict(event), dict(data.events[-1])])
    result = build_recent_limit_up_returns(data)
    row, = result["items"]
    assert result["candidate_count"] == result["analyzed_count"] == 1
    assert result["missing_count"] == 0
    assert row["return_pct"] == pytest.approx(30)
    assert row["start_date"] == data.calendar[-6]
    assert row["window_start_date"] == data.calendar[-5]
    assert row["end_date"] == data.calendar[-1]
    assert row["start_close"] == 10 and row["end_close"] == 13
    assert row["observed_intervals"] == 5
    assert row["limit_up_count"] == 2
    assert row["last_limit_date"] == data.calendar[-1]


def test_display_rounding_does_not_change_strict_return_filter():
    from app.agents.react_runtime.rendering import cell

    data = dataset(1)
    data.bars[-1]["close"] = 12.000004
    result = build_recent_limit_up_returns(data)
    store = EvidenceStore()
    source = store.add(tool="recent_limit_up_returns", payload=result, state="ok", arguments={})
    selected = store.compute(Compute(evidence_id=source,
        filters=[Filter(field="return_pct", operator="gt", value=20)]))
    row, = store.get(selected)["rows"]
    assert row["return_pct"] == pytest.approx(20.00004)
    assert row["return_pct"] > 20
    assert cell(row["return_pct"], field="return_pct") == "20.0"


def test_all_candidates_survive_scan_then_strict_threshold_and_sort(monkeypatch):
    data = dataset(123)
    for bar in data.bars:
        if bar["trade_date"] == data.calendar[-1]:
            index = int(bar["symbol"]) - 600001
            bar["close"] = 12 if index == 0 else 11.9 if index == 1 else 12.1 + index / 100
    result = registry(monkeypatch, data).recent_limit_up_returns()
    payload = result.output
    assert len(payload["items"]) == payload["matched_count"] == 123
    assert payload["candidate_count"] == payload["analyzed_count"] == 123
    assert result.trace().output["candidate_count"] == 123
    store = EvidenceStore()
    source = store.add(tool="recent_limit_up_returns", payload=payload, state="ok", arguments={})
    selected = store.compute(Compute(evidence_id=source,
        filters=[Filter(field="return_pct", operator="gt", value=20)], sort_by="return_pct"))
    rows = store.get(selected)["rows"]
    assert len(rows) == 121
    assert {"600001", "600002"}.isdisjoint(row["symbol"] for row in rows)
    assert rows[0]["symbol"] == "600123"
    assert all(left["return_pct"] >= right["return_pct"] for left, right in zip(rows, rows[1:]))
    assert not store.view(selected)["source_truncated"]
    contract = OutputContract(table_required=True, field_requests=[
        {"requested": "股票代码", "field": "symbol"}, {"requested": "近5日累计涨幅", "field": "return_pct"}])
    assert contract.field_resolution.status == "resolved"
    final = Finish(status="complete", answer="{{evidence_table}}", table=EvidenceTable(
        evidence_id=selected, columns=[EvidenceColumn(field="symbol", label="股票代码"),
            EvidenceColumn(field="return_pct", label="近5日累计涨幅")]))
    answer = render_answer(final, store, contract)
    assert len(answer.splitlines()) == 123  # Two headings plus all 121 selected stocks.
    assert "600123" in answer and "600003" in answer and "600001" not in answer


def test_cutoff_excludes_future_prices_events_old_candidates_and_failed_seals():
    data = dataset(1)
    cutoff = data.calendar[-2]
    data.events.append({**data.events[-1], "trade_date": cutoff})
    data.events.extend([
        {"symbol": "600002", "name": "未来", "trade_date": data.calendar[-1], "closed_limit": 1},
        {"symbol": "600003", "name": "过旧", "trade_date": data.calendar[1], "closed_limit": 1},
        {"symbol": "600004", "name": "炸板", "trade_date": cutoff, "closed_limit": 0},
    ])
    for bar in data.bars:
        if bar["trade_date"] == cutoff:
            bar["close"] = 15
        elif bar["trade_date"] == data.calendar[-1]:
            bar["close"] = 100
    result = build_recent_limit_up_returns(data, end_date=date.fromisoformat(cutoff))
    row, = result["items"]
    assert result["candidate_count"] == 1
    assert row["symbol"] == "600001"
    assert row["end_date"] == cutoff
    assert row["return_pct"] == pytest.approx(50)


@pytest.mark.parametrize("missing_offset", [-6, -3, -1], ids=["baseline", "middle", "cutoff"])
def test_missing_required_close_does_not_borrow_an_older_observation(missing_offset):
    data = dataset()
    missing_day = data.calendar[missing_offset]
    data = replace(data, bars=[bar for bar in data.bars
        if (bar["symbol"], bar["trade_date"]) != ("600001", missing_day)])
    result = build_recent_limit_up_returns(data)
    assert [row["symbol"] for row in result["items"]] == ["600002"]
    assert result["candidate_count"] == 2
    assert result["analyzed_count"] == result["missing_count"] == 1
    assert result["matched_count"] == len(result["items"]) == 1
    assert result["data_missing"]
    assert result["missing_items"][0]["symbol"] == "600001"
    assert result["missing_items"][0]["reasons"]


@pytest.mark.parametrize("source", ["unknown", "", "hithink-finance.market.history.qfq"])
def test_unknown_or_mixed_adjustment_basis_is_missing(source):
    data = dataset(1)
    data.bars[-1]["source"] = source
    result = build_recent_limit_up_returns(data)
    assert result["items"] == []
    assert result["missing_count"] == 1
    assert result["data_missing"] and result["missing_items"][0]["reasons"]


def test_known_unadjusted_history_and_snapshot_can_share_a_window():
    data = dataset(1)
    data.bars[-1]["source"] = "hithink-finance.market.snapshot"
    result = build_recent_limit_up_returns(data)
    assert result["items"][0]["return_pct"] == pytest.approx(30)
    assert result["missing_count"] == 0


@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf")])
def test_invalid_prices_do_not_become_a_return(close):
    data = dataset(1)
    data.bars[-6]["close"] = close
    result = build_recent_limit_up_returns(data)
    assert result["items"] == []
    assert result["missing_count"] == 1 and result["data_missing"]


def test_missing_candidate_day_remains_partial_after_filtering():
    data = dataset(1)
    missing_day = data.calendar[-3]
    data = replace(data, events=[row for row in data.events if row["trade_date"] != missing_day],
                   event_dates=[day for day in data.event_dates if day != missing_day])
    result = build_recent_limit_up_returns(data)
    assert len(result["items"]) == 1
    assert result["data_missing"]
    store = EvidenceStore()
    source = store.add(tool="recent_limit_up_returns", payload=result, state="partial", arguments={})
    selected = store.compute(Compute(evidence_id=source,
        filters=[Filter(field="return_pct", operator="gt", value=20)]))
    assert store.view(selected)["result_state"] == "partial"
    assert store.view(selected)["data_missing"] == result["data_missing"]


def test_insufficient_calendar_never_relabels_four_intervals_as_five():
    data = dataset(1)
    data = replace(data, calendar=data.calendar[-5:], event_dates=data.event_dates[-5:],
        bars=data.bars[-5:], events=data.events[-1:])
    result = build_recent_limit_up_returns(data, recent_trade_days=5)
    assert result["items"] == []
    assert result["data_missing"]


def test_event_date_with_no_bars_marketwide_is_not_skipped_in_common_calendar():
    data = dataset()
    missing_day = data.calendar[-3]
    data = replace(data, calendar=[day for day in data.calendar if day != missing_day],
        bars=[bar for bar in data.bars if bar["trade_date"] != missing_day])
    result = build_recent_limit_up_returns(data)
    assert result["items"] == [] and result["missing_count"] == 2
    assert missing_day in result["return_trade_dates"]
    assert result["start_date"] == data.event_dates[-6]


def test_duplicate_daily_close_is_ambiguous_even_when_values_match():
    data = dataset(1)
    data.bars.append(dict(data.bars[-3]))
    result = build_recent_limit_up_returns(data)
    assert result["items"] == [] and result["missing_count"] == 1
    assert result["missing_items"][0]["reasons"]


def test_explicit_unavailable_cutoff_never_falls_back_to_last_available_day():
    data = dataset(1)
    cutoff = data.latest_data_date + timedelta(days=1)
    result = build_recent_limit_up_returns(data, end_date=cutoff)
    assert result["items"] == [] and result["data_missing"]
    assert result["end_date"] == cutoff.isoformat()


def test_v1_catalog_and_policy_bind_an_explicit_observation_date(monkeypatch):
    data = dataset()
    target = registry(monkeypatch, data)
    assert target.is_enabled("recent_limit_up_returns")
    assert "recent_limit_up_returns" in {tool.name for tool in target.schemas()}
    assert TOOL_COLLECTIONS["recent_limit_up_returns"] == "items"
    gateway = ToolGateway(target, EvidenceStore())
    cutoff = date.fromisoformat(data.calendar[-1])
    gateway.date_scope = DateScope(mode="dates", dates=[cutoff])
    args = gateway.validate({"name": "recent_limit_up_returns", "args": {}})
    assert args["end_date"] == cutoff.isoformat()
    result, payload, state = gateway.execute("recent_limit_up_returns", args)
    assert result.input["end_date"] == cutoff.isoformat()
    assert state == "ok" and len(payload["items"]) == 2
    with pytest.raises(ValueError):
        gateway.validate({"name": "recent_limit_up_returns", "args": {"end_date": data.calendar[-2]}})


def test_explicit_event_dates_cannot_be_replaced_with_a_rolling_candidate_pool(monkeypatch):
    gateway = ToolGateway(registry(monkeypatch, dataset()), EvidenceStore())
    gateway.date_scope = DateScope(mode="dates", basis="event", dates=[date(2026, 10, 12)],
                                   observation_cutoff=date(2026, 10, 16))
    with pytest.raises(ValueError):
        gateway.validate({"name": "recent_limit_up_returns", "args": {"end_date": "2026-10-16"}})
    stock_args = gateway.validate({"name": "stock_kline", "args": {"symbol": "600001"}})
    assert stock_args["end_date"] == "2026-10-16"


@pytest.mark.parametrize("arguments", [{"recent_trade_days": 0}, {"return_trade_days": 0}])
def test_nonpositive_windows_are_rejected(arguments):
    with pytest.raises(ValueError):
        build_recent_limit_up_returns(dataset(), **arguments)
