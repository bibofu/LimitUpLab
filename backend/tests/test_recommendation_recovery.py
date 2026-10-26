"""Failed recommendation drafts may recover without changing final-time rules."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.collectors.hithink_finance_collector import HithinkFinanceError
from app.models import RecommendationIntelligenceResponse, StockNewsFacts
from app.services import recommendation_intelligence as service
from scripts import run_recommendation_refresh_loop as worker


BASE = date(2026, 10, 26)
TARGET = date(2026, 10, 27)
START = datetime.fromisoformat("2026-10-26T16:00:00+08:00")


def draft(*, status="partial", stage="draft", refreshed_at=START):
    return RecommendationIntelligenceResponse(
        refresh_id="stored", refreshed_at=refreshed_at, interval_minutes=1440,
        relay_base_date=BASE, target_trade_date=TARGET, stage=stage, status=status,
        warnings=["prior failure"] if status == "partial" else [],
    )


@pytest.fixture
def refresh_setup(monkeypatch):
    repository = Mock()
    repository.get_latest.return_value = draft()
    repository.get_final.return_value = None
    monkeypatch.setattr(service, "_load_base_candidates", Mock(return_value=([], BASE, [])))
    monkeypatch.setattr(service, "collect_a_share_trade_dates", Mock(return_value=[BASE, TARGET]))
    collector = Mock()
    monkeypatch.setattr(service, "HithinkFinanceCollector", collector)

    def refresh(now):
        return service.refresh_recommendation_intelligence(
            now=now, snapshot_repository=repository, first_board_repository=Mock(),
            limit_up_repository=Mock(), max_workers=1,
        )

    return repository, collector, refresh


def test_partial_draft_waits_for_cooldown_then_recovers(refresh_setup):
    repository, collector, refresh = refresh_setup
    previous = repository.get_latest.return_value
    assert refresh(START + timedelta(minutes=4, seconds=59)) is previous
    collector.assert_not_called()
    repository.save.assert_not_called()

    response = refresh(START + timedelta(minutes=5))
    assert response.refresh_id != previous.refresh_id
    assert response.status == "complete" and response.warnings == []
    collector.assert_called_once()
    repository.save.assert_called_once_with(response)


def test_complete_draft_stays_frozen_before_0800(refresh_setup):
    repository, collector, refresh = refresh_setup
    previous = draft(status="complete")
    repository.get_latest.return_value = previous
    assert refresh(datetime.fromisoformat("2026-10-27T07:59:59+08:00")) is previous
    collector.assert_not_called()


def test_0800_refresh_is_not_delayed_by_partial_cooldown(refresh_setup):
    repository, collector, refresh = refresh_setup
    repository.get_latest.return_value = draft(
        refreshed_at=datetime.fromisoformat("2026-10-27T07:59:00+08:00"),
    )
    response = refresh(datetime.fromisoformat("2026-10-27T08:00:00+08:00"))
    assert response.refresh_id != "stored"
    collector.assert_called_once()


def test_recovery_at_market_open_collects_nothing(refresh_setup):
    repository, collector, refresh = refresh_setup
    response = refresh(datetime.fromisoformat("2026-10-27T09:30:00+08:00"))
    assert response.stage == "missed_cutoff"
    collector.assert_not_called()


def test_provider_io_crossing_open_discards_new_evidence(refresh_setup, monkeypatch):
    repository, collector, refresh = refresh_setup
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [
        datetime.fromisoformat("2026-10-27T09:29:00+08:00"),
        datetime.fromisoformat("2026-10-27T09:29:01+08:00"),
        datetime.fromisoformat("2026-10-27T09:30:01+08:00"),
    ]
    monkeypatch.setattr(service, "datetime", clock)
    response = refresh(None)
    assert response.stage == "missed_cutoff"
    assert response.refreshed_at == START and response.items == []
    collector.assert_called_once()


def test_financial_rate_limit_is_not_reported_as_news_failure_or_cache():
    candidate = service._BaseCandidate("relay", BASE, "001317", "样本", "行业", None, 1, 80)
    news = StockNewsFacts(symbol=candidate.symbol, name=candidate.name, fetched_at=START,
                          window_days=7, cache_status="fresh")
    financial = Mock(side_effect=HithinkFinanceError('{"ok":false}', code="FUYAO_429"))
    evidence = service._load_candidate_evidence(
        candidate, refreshed_at=START, previous_financial=None, previous_news=None,
        news_collector=lambda *_: news, financial_collector=financial,
    )
    assert evidence.financial_failed and not evidence.news_failed
    assert evidence.financial_report is None and not evidence.financial_cached
    assert evidence.errors == ["财报刷新失败：同花顺请求触发限流，本轮未取得新数据。"]
    assert service._evidence_failure_warnings({candidate.symbol: evidence}) == [
        "1 只股票的财报刷新失败；0 只沿用已有缓存，1 只无可用缓存。",
    ]


def test_failed_source_cache_counts_only_retained_evidence():
    candidate = service._BaseCandidate("relay", BASE, "001317", "样本", "行业", None, 1, 80)
    cached_news = SimpleNamespace(items=[object()])
    cached_financial = SimpleNamespace(fetched_at=START - timedelta(days=2))
    failed = Mock(side_effect=RuntimeError("upstream failure"))
    evidence = service._load_candidate_evidence(
        candidate, refreshed_at=START, previous_financial=cached_financial,
        previous_news=cached_news, news_collector=failed, financial_collector=failed,
    )
    assert evidence.news is cached_news and evidence.financial_report is cached_financial
    assert evidence.news_cached and evidence.financial_cached
    assert service._evidence_failure_warnings({candidate.symbol: evidence}) == [
        "1 只股票的新闻刷新失败；1 只沿用已有缓存，0 只无可用缓存。",
        "1 只股票的财报刷新失败；1 只沿用已有缓存，0 只无可用缓存。",
    ]


def test_empty_financial_response_retains_previous_report():
    candidate = service._BaseCandidate("relay", BASE, "001317", "样本", "行业", None, 1, 80)
    previous = SimpleNamespace(fetched_at=START - timedelta(days=2))
    news = SimpleNamespace(cache_status="fresh", items=[])
    evidence = service._load_candidate_evidence(
        candidate, refreshed_at=START, previous_financial=previous, previous_news=None,
        news_collector=lambda *_: news, financial_collector=lambda *_: [],
    )
    assert evidence.financial_report is previous and evidence.financial_cached
    assert evidence.financial_failed


@pytest.mark.parametrize("result", [draft(), RuntimeError("temporary failure")])
def test_worker_uses_bounded_recovery_backoff(result):
    assert worker._next_refresh_wait(result, 1, START) == 300
    assert worker._next_refresh_wait(result, 2, START) == 600
    assert worker._next_refresh_wait(result, 3, START) == 1200
    assert worker._next_refresh_wait(result, 4, START) == 16 * 3600


def test_recovery_never_delays_0800_and_stops_after_open_or_final():
    before = datetime.fromisoformat("2026-10-27T07:59:30+08:00")
    assert worker._next_refresh_wait(draft(), 2, before) == 30
    opened = datetime.fromisoformat("2026-10-27T09:30:00+08:00")
    assert worker._next_refresh_wait(draft(), 1, opened) == 22.5 * 3600
    assert worker._next_refresh_wait(draft(stage="final"), 1, START) == 16 * 3600
    assert worker._next_refresh_wait(draft(status="complete"), 1, START) == 16 * 3600
