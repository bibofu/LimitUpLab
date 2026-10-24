"""An Agent return always names the exact observations that support it."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.kline_presentation import kline_agent_view
from app.agents.tool_payload import payload_of
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import StockKLineFacts


def payload():
    return {"symbol": "600123", "requested_days": 5, "requested_end_date": "2026-10-15",
        "data_as_of": "2026-10-15", "data_fresh": True, "trend": "rising", "latest_close": 25.58,
        "return_5d_pct": 34.561, "return_10d_pct": 40.0, "return_20d_pct": 50.0,
        "bars": [{"trade_date": day, "close": close, "open": close, "high": close,
                  "low": close, "volume": 100, "source": "hithink-finance.market.history.none"}
                 for day, close in [("2026-10-09", 20.91), ("2026-10-12", 21.2),
                                    ("2026-10-13", 22), ("2026-10-14", 24), ("2026-10-15", 25.58)]]}


def test_five_visible_bars_are_four_intervals_not_the_five_interval_public_metric():
    raw = payload()
    view = payload_of(ToolResult(name="stock_kline", input={}, output=raw, summary="test"))
    window = view["display_window_return"]
    assert window["start_date"] == "2026-10-09" and window["end_date"] == "2026-10-15"
    assert window["start_close"] == 20.91 and window["end_close"] == 25.58
    assert window["observations"] == 5 and window["observed_intervals"] == 4
    assert window["return_pct"] == 22.334
    assert view["return_windows"] == []
    assert not {"return_5d_pct", "return_10d_pct", "return_20d_pct"} & view.keys()
    assert raw["return_5d_pct"] == 34.561, "Public facts remain unchanged"


def test_six_visible_bars_prove_the_distinct_five_interval_window():
    raw = payload()
    raw["bars"].insert(0, {**raw["bars"][0], "trade_date": "2026-10-08", "close": 19.01})
    window = kline_agent_view(raw)["return_windows"][0]
    assert window["observations"] == 6 and window["observed_intervals"] == 5
    assert window["start_date"] == "2026-10-08" and window["start_close"] == 19.01
    assert window["return_pct"] == 34.561


def test_incompatible_price_sources_do_not_create_a_comparable_return():
    raw = payload()
    raw["bars"][0]["source"] = "different-adjustment"
    assert kline_agent_view(raw)["display_window_return"] is None


def test_tool_summary_and_trace_do_not_reintroduce_unscoped_returns(monkeypatch):
    from app.agents import tools
    facts = StockKLineFacts.model_validate(payload())
    monkeypatch.setattr(tools, "build_stock_kline_facts", lambda **kwargs: facts)
    registry = SimpleNamespace(resolve_stock_symbol=lambda value: value, events=[], first_board_repository=None)
    result = AgentToolRegistry.stock_kline(registry, "600123", days=5, end_date=date(2026, 10, 15))
    assert "34.561" not in result.summary and "5d=" not in result.summary
    assert not {"return_5d_pct", "return_10d_pct", "return_20d_pct"} & result.trace_output.keys()
    assert result.trace_output["display_window_return"]["return_pct"] == 22.334
    assert result.output.return_5d_pct == 34.561


def test_drawdowns_keep_the_peak_before_the_trough_and_distinguish_price_bases():
    raw = {"max_drawdown_pct": -3.9, "bars": [
        {"trade_date": day, "close": close, "high": high, "low": low,
         "source": "hithink-finance.market.history.none"}
        for day, close, high, low in [
            ("2026-09-18", 1257.12, 1260, 1250),
            ("2026-09-24", 1240, 1250, 1230),
            ("2026-09-29", 1235.58, 1245, 1228.1),
            ("2026-10-19", 1330, 1348.6, 1320),
            ("2026-10-22", 1310, 1320, 1296),
            ("2026-10-23", 1335, 1340, 1320),
        ]
    ]}
    view = kline_agent_view(raw)
    close = view["close_max_drawdown"]
    assert (close["peak_date"], close["trough_date"]) == ("2026-09-18", "2026-09-29")
    assert (close["peak_price"], close["trough_price"], close["drawdown_pct"]) == (1257.12, 1235.58, -1.713)
    high_low = view["high_low_max_drawdown"]
    assert (high_low["peak_date"], high_low["trough_date"]) == ("2026-10-19", "2026-10-22")
    assert high_low["drawdown_pct"] == -3.9
    assert view["max_drawdown_pct"] == raw["max_drawdown_pct"]
    assert close["end_date"] == high_low["end_date"] == "2026-10-23"


def test_daily_high_low_drawdown_preserves_same_day_convention():
    raw = payload()
    raw["bars"][-1].update(high=30, low=24)
    view = kline_agent_view(raw)
    assert view["close_max_drawdown"]["drawdown_pct"] == 0
    high_low = view["high_low_max_drawdown"]
    assert high_low["drawdown_pct"] == -20
    assert high_low["peak_date"] == high_low["trough_date"] == "2026-10-15"


def test_unavailable_or_out_of_order_prices_do_not_invent_drawdown_endpoints():
    for issue in ("missing_high", "incompatible_source", "reversed_dates"):
        raw = payload()
        if issue == "missing_high":
            raw["bars"][1]["high"] = None
        elif issue == "incompatible_source":
            raw["bars"][1]["source"] = "different-adjustment"
        else:
            raw["bars"].reverse()
        view = kline_agent_view(raw)
        assert view["high_low_max_drawdown"] is None
        assert "high_low_max_drawdown" in view["data_missing"]
        if issue != "missing_high":
            assert view["close_max_drawdown"] is None
            assert "close_max_drawdown" in view["data_missing"]


@pytest.mark.parametrize("days", [
    ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"],
    ["2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08"],
])
def test_complete_row_count_does_not_verify_calendar_or_explain_date_gaps(days):
    raw = payload()
    raw.update(requested_days=len(days), requested_end_date=days[-1], data_as_of=days[-1],
               data_fresh=True, complete=True, source_truncated=False)
    for bar, day in zip(raw["bars"], days):
        bar["trade_date"] = day
    view = payload_of(ToolResult(name="stock_kline", input={}, output=raw, summary="test"))

    assert len(view["bars"]) == view["requested_days"]
    assert view["data_fresh"] is True and view["complete"] is True
    assert view["calendar_coverage_status"] == "unverified"
    assert view["display_window_return"]["observations"] == len(days)
    assert view["display_window_return"]["observed_intervals"] == len(days) - 1
    assert "authoritative trading calendar" in view["metric_definitions"]["calendar_coverage_status"]
    assert "calendar_coverage_status" not in raw, "Presentation scope must not mutate public facts"
