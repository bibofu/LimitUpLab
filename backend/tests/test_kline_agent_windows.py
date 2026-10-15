"""An Agent return always names the exact observations that support it."""

from datetime import date
from types import SimpleNamespace

from app.agents.kline_presentation import kline_agent_view
from app.agents.react_runtime.evidence import payload_of
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
