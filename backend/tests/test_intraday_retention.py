"""Rolling retention for the stock-detail minute-bar cache."""

from datetime import date, datetime

import pytest

from database_support import isolated_database_url, release_database_url

from app.models import StockIntradayKLineBar
from app.repositories import FirstBoardRepository
from app.services.intraday_retention import prune_intraday_bars


TRADE_DATES = [
    date(2026, 9, 28),
    date(2026, 9, 29),
    date(2026, 9, 30),
    date(2026, 10, 8),
    date(2026, 10, 9),
    date(2026, 10, 12),
    date(2026, 10, 13),
    date(2026, 10, 14),
    date(2026, 10, 15),
    date(2026, 10, 16),
]


def calendar_for(dates: list[date]):
    return lambda _start, _end: list(dates)


def test_keeps_the_last_seven_verified_sessions():
    deleted_at: list[date] = []

    def deleter(cutoff: date) -> int:
        deleted_at.append(cutoff)
        return 42

    result = prune_intraday_bars(
        repository=object(),
        trade_date=TRADE_DATES[-1],
        calendar_loader=calendar_for(TRADE_DATES),
        deleter=deleter,
    )

    assert result.keep_trade_days == 7
    assert result.cutoff_date == TRADE_DATES[-7]
    assert result.retained_trade_dates == tuple(TRADE_DATES[-7:])
    assert result.deleted_rows == 42
    assert result.warnings == ()
    assert deleted_at == [TRADE_DATES[-7]]


def test_exactly_seven_sessions_uses_the_first_as_cutoff():
    deleted_at: list[date] = []
    result = prune_intraday_bars(
        repository=object(),
        trade_date=TRADE_DATES[6],
        calendar_loader=calendar_for(TRADE_DATES[:7]),
        deleter=lambda cutoff: deleted_at.append(cutoff) or 0,
    )

    assert result.cutoff_date == TRADE_DATES[0]
    assert result.retained_trade_dates == tuple(TRADE_DATES[:7])
    assert deleted_at == [TRADE_DATES[0]]


def test_insufficient_calendar_coverage_skips_deletion():
    called = False

    def deleter(_cutoff: date) -> int:
        nonlocal called
        called = True
        return 1

    result = prune_intraday_bars(
        repository=object(),
        trade_date=TRADE_DATES[5],
        calendar_loader=calendar_for(TRADE_DATES[:6]),
        deleter=deleter,
    )

    assert result.cutoff_date is None
    assert result.deleted_rows == 0
    assert result.warnings
    assert "少于保留窗口" in result.warnings[0]
    assert called is False


def test_calendar_failure_skips_deletion():
    def failing_loader(_start: date, _end: date) -> list[date]:
        raise RuntimeError("calendar offline")

    result = prune_intraday_bars(
        repository=object(),
        trade_date=TRADE_DATES[-1],
        calendar_loader=failing_loader,
        deleter=lambda _cutoff: 1,
    )

    assert result.cutoff_date is None
    assert result.deleted_rows == 0
    assert "交易日历不可用" in result.warnings[0]


def test_target_date_must_be_in_the_calendar():
    result = prune_intraday_bars(
        repository=object(),
        trade_date=date(2026, 10, 19),
        calendar_loader=calendar_for(TRADE_DATES),
        deleter=lambda _cutoff: 1,
    )

    assert result.cutoff_date is None
    assert result.deleted_rows == 0
    assert "未覆盖目标交易日" in result.warnings[0]


def test_cleanup_failure_is_reported_without_claiming_success():
    result = prune_intraday_bars(
        repository=object(),
        trade_date=TRADE_DATES[-1],
        calendar_loader=calendar_for(TRADE_DATES),
        deleter=lambda _cutoff: (_ for _ in ()).throw(RuntimeError("database down")),
    )

    assert result.cutoff_date == TRADE_DATES[-7]
    assert result.deleted_rows == 0
    assert "分钟线清理失败" in result.warnings[0]


def test_retention_cannot_drop_below_the_five_day_chart():
    with pytest.raises(ValueError, match="at least 5"):
        prune_intraday_bars(
            repository=object(),
            trade_date=TRADE_DATES[-1],
            keep_trade_days=4,
        )


def test_repository_deletes_only_rows_before_the_cutoff():
    database_url = isolated_database_url()
    repository = FirstBoardRepository(database_url=database_url)
    try:
        for trade_date in TRADE_DATES:
            repository.replace_intraday_bars(
                symbol="600001",
                trade_date=trade_date,
                period_minutes=1,
                bars=[StockIntradayKLineBar(
                    timestamp=datetime.combine(trade_date, datetime.min.time()).replace(
                        hour=9, minute=31,
                    ),
                    open=10,
                    high=10.2,
                    low=9.9,
                    close=10.1,
                    volume=1_000,
                    amount=10_100,
                )],
                source="test",
            )

        result = prune_intraday_bars(
            repository=repository,
            trade_date=TRADE_DATES[-1],
            calendar_loader=calendar_for(TRADE_DATES),
        )

        assert result.deleted_rows == 3
        assert repository.list_intraday_bars(
            symbol="600001", trade_date=TRADE_DATES[2], period_minutes=1,
        ) == []
        retained = repository.list_intraday_bars(
            symbol="600001", trade_date=TRADE_DATES[3], period_minutes=1,
        )
        assert len(retained) == 1
    finally:
        release_database_url(database_url)
