"""Offline calendar and restart-boundary regressions for recommendation drafts."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models import RecommendationIntelligenceItem, RecommendationIntelligenceResponse, StockNewsFacts
from app.repositories import SQLiteFirstBoardRepository, SQLiteRecommendationIntelligenceRepository
from app.services import recommendation_intelligence as service


BASE = date(2026, 9, 30)
TARGET = date(2026, 10, 8)


@pytest.mark.parametrize(("base", "target"), [
    (date(2026, 9, 28), date(2026, 9, 29)),
    (date(2026, 9, 18), date(2026, 9, 21)),
    (BASE, TARGET),
    (date(2026, 12, 31), date(2027, 1, 4)),
])
def test_target_uses_next_calendar_session_including_holiday_and_year_boundary(monkeypatch, base, target):
    calendar = Mock(return_value=[target, base, target])
    monkeypatch.setattr(service, "collect_a_share_trade_dates", calendar)

    assert service._target_trade_date(base_trade_date=base) == target
    start, end = calendar.call_args.args
    assert start == base and end >= target


def test_no_base_does_not_request_calendar(monkeypatch):
    calendar = Mock(side_effect=AssertionError("no calendar without a base"))
    monkeypatch.setattr(service, "collect_a_share_trade_dates", calendar)
    assert service._target_trade_date(base_trade_date=None) is None
    calendar.assert_not_called()


def snapshot(*, target=TARGET, stage="draft", with_items=False):
    timestamp = datetime.fromisoformat("2026-09-30T16:00:00+08:00")
    if stage == "final":
        timestamp = datetime.fromisoformat("2026-10-08T08:00:00+08:00")
    return RecommendationIntelligenceResponse(
        refresh_id="stored", refreshed_at=timestamp, interval_minutes=1440,
        relay_base_date=BASE, target_trade_date=target, stage=stage,
        finalized_at=timestamp if stage == "final" else None,
        status="partial", warnings=["existing warning"], items=[
            RecommendationIntelligenceItem(
                base_trade_date=BASE, symbol="600101", name="样本",
                rank=1, base_score=80, refreshed_at=timestamp,
            ),
        ] if with_items else [],
        prediction_provenance={
            "version": service.TIME_CONTRACT_VERSION, "stage": "premarket_final",
            "target_trade_date": target.isoformat(), "information_cutoff_at": timestamp.isoformat(),
            "calendar_verified": True, "calendar_trade_dates": [BASE.isoformat(), TARGET.isoformat()],
        } if stage == "final" else {},
    )


@pytest.fixture
def isolated_refresh(tmp_path, monkeypatch):
    database = tmp_path / "recommendations.sqlite"
    repository = SQLiteRecommendationIntelligenceRepository(database)
    first = SQLiteFirstBoardRepository(database)
    candidate = service._BaseCandidate("relay", BASE, "600101", "样本", "行业", None, 1, 80)
    monkeypatch.setattr(service, "_load_base_candidates", Mock(return_value=([candidate], BASE, [])))
    monkeypatch.setattr(service, "collect_a_share_trade_dates", Mock(return_value=[BASE, TARGET]))
    collector = Mock(side_effect=AssertionError("fresh evidence must not be collected"))
    monkeypatch.setattr(service, "HithinkFinanceCollector", collector)

    def refresh(now):
        return service.refresh_recommendation_intelligence(
            now=now, snapshot_repository=repository,
            first_board_repository=first, limit_up_repository=Mock(),
        )

    return repository, refresh, collector


@pytest.mark.parametrize("calendar_result", [
    RuntimeError("offline calendar failure"), [], [BASE], [TARGET],
    [BASE, date(2027, 1, 4)],
])
def test_unverified_calendar_records_unknown_target_and_preserves_history(isolated_refresh, monkeypatch, calendar_result):
    repository, refresh, collector = isolated_refresh
    previous = snapshot(target=date(2026, 10, 1), with_items=True)
    repository.save(previous)
    calendar = Mock(**({"side_effect": calendar_result} if isinstance(calendar_result, Exception)
                       else {"return_value": calendar_result}))
    monkeypatch.setattr(service, "collect_a_share_trade_dates", calendar)

    response = refresh(datetime.fromisoformat("2026-10-08T10:00:00+08:00"))

    assert repository.get_latest() == response
    assert response.target_trade_date is None
    assert response.relay_base_date == BASE and response.stage == "draft"
    assert response.status == "partial" and response.items == [] and response.finalized_at is None
    assert any("交易日历不可用" in warning and "目标交易日未确认" in warning for warning in response.warnings)
    displayed = repository.get_latest_displayable()
    assert displayed.model_dump(exclude={"display_context"}) == previous.model_dump(exclude={"display_context"})
    assert displayed.display_context.latest_target_trade_date is None
    assert displayed.display_context.latest_warnings == response.warnings
    collector.assert_not_called()


@pytest.mark.parametrize("stage", ["draft", "missed_cutoff"])
def test_wrong_holiday_target_recovers_after_open_without_new_evidence(isolated_refresh, stage):
    repository, refresh, collector = isolated_refresh
    repository.save(snapshot(target=date(2026, 10, 1), stage=stage))

    response = refresh(datetime.fromisoformat("2026-10-08T10:00:00+08:00"))

    assert response.target_trade_date == TARGET
    assert response.relay_base_date == BASE
    assert response.stage == "missed_cutoff" and response.status == "partial"
    assert response.items == [] and response.finalized_at is None
    assert not service.should_finalize_recommendation_intelligence(
        response, now=datetime.fromisoformat("2026-10-08T10:00:00+08:00"),
    )
    assert repository.get_latest() == response
    collector.assert_not_called()


def test_wrong_holiday_target_can_be_refreshed_before_real_market_open(isolated_refresh, monkeypatch):
    repository, _refresh, _collector = isolated_refresh
    previous = snapshot(target=date(2026, 10, 1), stage="missed_cutoff")
    repository.save(previous)
    now = datetime.fromisoformat("2026-10-08T08:10:00+08:00")
    monkeypatch.setattr(service, "HithinkFinanceCollector", Mock())
    quotes = Mock(return_value=SimpleNamespace(items=[], captured_at=now))

    response = service.refresh_recommendation_intelligence(
        now=now, max_workers=1, snapshot_repository=repository,
        first_board_repository=SQLiteFirstBoardRepository(repository.database_path),
        limit_up_repository=Mock(), quote_collector=quotes,
        news_collector=lambda symbol, name: StockNewsFacts(
            symbol=symbol, name=name, fetched_at=now, window_days=7, cache_status="fresh",
        ),
        financial_collector=lambda _symbol: [], dragon_tiger_collector=lambda _date: {},
        popularity_collector=lambda: {},
    )

    assert response.target_trade_date == TARGET and response.stage == "draft"
    assert response.refreshed_at == now and response.finalized_at is None
    assert response.items[0].symbol == "600101"
    quotes.assert_called_once()
    assert previous.target_trade_date == date(2026, 10, 1)


def test_verified_final_is_reused_without_rewriting_or_collecting(isolated_refresh):
    repository, refresh, collector = isolated_refresh
    previous = snapshot(stage="final")
    assert repository.save_final(previous)

    response = refresh(datetime.fromisoformat("2026-10-08T10:00:00+08:00"))

    assert response == previous
    assert repository.get_latest() == previous
    assert repository.get_final(TARGET.isoformat()) == previous
    collector.assert_not_called()


def test_calendar_failure_never_overwrites_immutable_final(isolated_refresh, monkeypatch):
    repository, refresh, collector = isolated_refresh
    previous = snapshot(stage="final")
    assert repository.save_final(previous)
    monkeypatch.setattr(service, "collect_a_share_trade_dates", Mock(side_effect=RuntimeError("offline")))

    response = refresh(datetime.fromisoformat("2026-10-08T10:00:00+08:00"))

    assert response.target_trade_date is None and response.stage == "draft"
    assert repository.get_final(TARGET.isoformat()) == previous
    collector.assert_not_called()


def test_calendar_io_crossing_open_rechecks_clock_before_evidence(isolated_refresh, monkeypatch):
    repository, refresh, collector = isolated_refresh
    repository.save(snapshot())
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [
        datetime.fromisoformat("2026-10-08T09:29:59+08:00"),
        datetime.fromisoformat("2026-10-08T09:30:00+08:00"),
    ]
    monkeypatch.setattr(service, "datetime", clock)

    response = refresh(None)

    assert response.stage == "missed_cutoff"
    assert response.refreshed_at == datetime.fromisoformat("2026-09-30T16:00:00+08:00")
    assert response.finalized_at is None
    collector.assert_not_called()
