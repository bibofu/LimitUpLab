from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts.dev_check import refresh_recommendation_after_update
from scripts.update_daily_data import DailyUpdateReport


CN_TZ = ZoneInfo("Asia/Shanghai")


def _ready_report() -> DailyUpdateReport:
    return DailyUpdateReport(
        trade_date="2026-09-18",
        health={
            "raw_events_ready": True,
            "first_board_features_ready": True,
        },
    )


def test_refreshes_recommendation_immediately_after_ready_data_update() -> None:
    limit_repository = object()
    first_board_repository = object()
    received: list[dict[str, object]] = []

    def fake_refresh(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(
            status="complete",
            refresh_id="refresh-20260918",
            stage="draft",
            relay_base_date=date(2026, 9, 18),
            target_trade_date=date(2026, 9, 21),
            refreshed_at=datetime(2026, 9, 18, 17, 30, tzinfo=CN_TZ),
            items=[SimpleNamespace(), SimpleNamespace()],
        )

    result = refresh_recommendation_after_update(
        update_report=_ready_report(),
        limit_up_repository=limit_repository,
        first_board_repository=first_board_repository,
        refresher=fake_refresh,
    )

    assert received == [
        {
            "limit_up_repository": limit_repository,
            "first_board_repository": first_board_repository,
        }
    ]
    assert result["relay_base_date"] == "2026-09-18"
    assert result["target_trade_date"] == "2026-09-21"
    assert result["item_count"] == 2


def test_rejects_refresh_that_does_not_advance_to_updated_close() -> None:
    def stale_refresh(**_kwargs):
        return SimpleNamespace(relay_base_date=date(2026, 9, 17))

    with pytest.raises(RuntimeError, match="2026-09-17 != 2026-09-18"):
        refresh_recommendation_after_update(
            update_report=_ready_report(),
            limit_up_repository=object(),
            first_board_repository=object(),
            refresher=stale_refresh,
        )


def test_skips_refresh_until_close_data_is_ready() -> None:
    report = _ready_report()
    report.health["first_board_features_ready"] = False

    result = refresh_recommendation_after_update(
        update_report=report,
        limit_up_repository=object(),
        first_board_repository=object(),
        refresher=lambda **_kwargs: pytest.fail("refresh should not run"),
    )

    assert result["status"] == "skipped"
