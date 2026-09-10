"""Keep both public market summary routes behaviorally equivalent."""

from unittest.mock import Mock, call

import pytest
from app.models import MarketIndexSnapshot, MarketSummary
from app.routers import market
from app.services.analysis import latest_trade_date, summarize_market
from app.services.sample_data import SAMPLE_EVENTS


# Regression scenario: summary and overview preserve the same contract.
@pytest.mark.parametrize("scenario", ["normal", "empty_indices", "index_failure", "empty_events"])
def test_summary_and_overview_preserve_the_same_contract(monkeypatch, scenario):
    events = [] if scenario == "empty_events" else SAMPLE_EVENTS
    repository = Mock()
    repository.list_events.return_value = events
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    monkeypatch.setattr(market, "get_limit_up_repository", lambda: repository)
    indices = [MarketIndexSnapshot(
        name="测试指数", symbol="000001.SH",
        trade_date=latest_trade_date(SAMPLE_EVENTS), close=3000,
        change_pct=1.2, trend=[2980, 3000], source="test",
    )] if scenario == "normal" else []
    collector = Mock(return_value=indices)
    if scenario == "index_failure":
        collector.side_effect = RuntimeError("index source unavailable")
    monkeypatch.setattr(market, "collect_market_indices", collector)
    routes = {route.path: route for route in market.router.routes}
    endpoints = []
    for path in ("/summary", "/overview"):
        route = routes[path]
        assert route.methods == {"GET"}
        assert route.response_model is MarketSummary
        endpoints.append(route.endpoint)
    if scenario == "empty_events":
        # Preserve the existing failure; do not manufacture an empty market.
        for endpoint in endpoints:
            with pytest.raises(ValueError, match="max"):
                endpoint()
        collector.assert_not_called()
    else:
        expected = summarize_market(events, indices=indices).model_dump(mode="json")
        for endpoint in endpoints:
            assert endpoint().model_dump(mode="json") == expected
        assert collector.call_args_list == [call(latest_trade_date(events))] * 2
    assert repository.list_events.call_count == 2
