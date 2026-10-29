"""Stock activity model view keeps summaries instead of duplicating K-line bars."""

import json
from types import SimpleNamespace

from app.agents.runner.tool_view import model_result


def _payload() -> dict:
    return {
        "symbol": "600519",
        "name": "贵州茅台",
        "fetched_at": "2026-10-29T00:00:00+00:00",
        "data_as_of": "2026-10-28",
        "kline": {
            "symbol": "600519",
            "requested_days": 20,
            "trend": "oscillating",
            "latest_close": 1330.75,
            "ma5": 1338.02,
            "bars": [
                {"trade_date": f"2026-09-{day:02d}", "close": 1200 + day}
                for day in range(1, 21)
            ],
        },
        "recent_limit_up_events": [],
        "rating_context": {},
        "news": {
            "symbol": "600519",
            "items": [{"title": f"news-{index}"} for index in range(12)],
            "data_missing": [],
        },
        "data_missing": ["本地没有该股票的近期涨停事件。"],
    }


def test_stock_activity_model_view_drops_bars_and_bounds_news():
    result = SimpleNamespace(summary="已汇总", input={"symbol": "600519"})

    observation = model_result("stock_activity", result, _payload(), "success")

    row = observation["rows"][0]
    assert "bars" not in row["kline"]
    assert row["kline"]["bars_omitted"] is True
    assert row["kline"]["trend"] == "oscillating"
    assert len(row["news"]["items"]) == 8
    assert "stock_kline" in row["model_note"]
    assert observation["row_count"] == 1
    assert observation["truncated"] is False
    assert len(json.dumps(observation, ensure_ascii=False)) < 8000
