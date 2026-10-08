from datetime import date
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.tools import AgentToolRegistry
from app.routers.analysis import router
from app.services.analysis import calculate_daily_board_promotion
from app.services.daily_promotion import build_daily_promotion_report
from app.services.promotion_calendar import PromotionCalendar
from app.services.sample_data import SAMPLE_EVENTS


BASE = date(2026, 9, 30)
NEXT = date(2026, 10, 8)


def event(day, symbol="000001", height=1, closed=True):
    return SAMPLE_EVENTS[0].model_copy(update={
        "trade_date": day, "symbol": symbol, "board_height": height,
        "closed_limit": closed,
    })


def test_long_holiday_counts_promotions_on_actual_next_session():
    events = [event(BASE), event(BASE, "000002"), event(NEXT, height=2)]
    report = build_daily_promotion_report(events, trade_dates=[BASE, NEXT])
    assert report.latest_event_date == NEXT
    assert not report.warnings
    assert len(report.items) == 1
    stat = report.items[0]
    assert (stat.previous_trade_date, stat.trade_date) == (BASE, NEXT)
    assert (stat.promoted_count, stat.sample_size, stat.probability) == (1, 2, 0.5)


def test_missing_weekday_is_not_bridged_even_within_four_days():
    first, missing, last = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)
    report = build_daily_promotion_report(
        [event(first), event(last, height=2)], trade_dates=[first, missing, last],
    )
    assert report.items == []
    assert "2026-10-13" in report.warnings[0]
    assert "未计算" in report.warnings[0]


def test_observed_all_failed_day_is_zero_not_missing():
    report = build_daily_promotion_report(
        [event(BASE), event(NEXT, closed=False)], trade_dates=[BASE, NEXT],
    )
    assert report.items[0].probability == 0
    assert not report.warnings


def test_unknown_calendar_never_guesses_adjacency():
    assert calculate_daily_board_promotion([event(BASE), event(NEXT, height=2)]) == []
    with patch("app.services.daily_promotion.load_promotion_calendar", return_value=PromotionCalendar((), ("交易日历不可用",))):
        report = build_daily_promotion_report([event(BASE), event(NEXT, height=2)])
    assert report.items == []
    assert report.warnings == ["交易日历不可用"]


def test_end_date_filters_future_data_before_calendar_load():
    with patch("app.services.daily_promotion.load_promotion_calendar", return_value=PromotionCalendar((BASE,))) as load:
        report = build_daily_promotion_report([event(BASE), event(NEXT)], end_date=BASE)
    load.assert_called_once_with(BASE, BASE)
    assert report.latest_event_date == BASE
    assert report.items == []


def test_requested_end_beyond_latest_data_is_not_complete():
    previous = date(2026, 9, 29)
    report = build_daily_promotion_report(
        [event(previous), event(BASE, height=2)], end_date=NEXT,
        trade_dates=[previous, BASE, NEXT],
    )
    assert report.items[-1].trade_date == BASE
    assert any("请求截至 2026-10-08" in warning for warning in report.warnings)


def test_recent_window_does_not_fill_missing_sessions_with_older_statistics():
    dates = [date(2026, 10, day) for day in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
    observed = dates[:6] + dates[-1:]
    report = build_daily_promotion_report(
        [event(day) for day in observed], days=5, trade_dates=dates,
    )
    assert [stat.trade_date for stat in report.items] == [date(2026, 10, 19)]
    assert all(day.isoformat() in report.warnings[0] for day in dates[6:9])


def test_api_report_and_legacy_array_share_calendar_result():
    app = FastAPI()
    app.include_router(router)
    repo = Mock()
    repo.list_events.return_value = [event(BASE), event(NEXT, height=2)]
    with patch("app.routers.analysis.get_limit_up_repository", return_value=repo), patch(
        "app.services.daily_promotion.load_promotion_calendar", return_value=PromotionCalendar((BASE, NEXT)),
    ), TestClient(app) as client:
        report = client.get("/daily-promotion-report?days=5").json()
        legacy = client.get("/daily-promotion?days=5").json()
    assert report["items"] == legacy
    assert report["latest_event_date"] == "2026-10-08"
    assert report["items"][0]["probability"] == 1


def test_agent_tool_exposes_calendar_gap_as_partial():
    registry = object.__new__(AgentToolRegistry)
    registry.events = [event(BASE), event(NEXT)]
    registry.first_board_repository = Mock()
    registry.first_board_repository.list_daily_bars_for_symbols.return_value = []
    with patch("app.services.daily_promotion.load_promotion_calendar", return_value=PromotionCalendar((), ("交易日历不可用",))):
        result = registry.daily_board_promotion()
    assert result.result_status == "partial"
    assert result.output == []
    assert result.trace_output["data_missing"] == ["交易日历不可用"]
    assert "交易日历不可用" in result.summary
