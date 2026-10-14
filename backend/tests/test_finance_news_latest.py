from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from app.models import FinanceNewsFacts, FinanceNewsItem
from app.services import finance_news_latest as latest


NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


def news(at=NOW, title="A股市场最新新闻"):
    return FinanceNewsFacts(
        fetched_at=at, window_hours=24, sources=["东方财富"],
        items=[FinanceNewsItem(
            title=title, summary="市场公开信息。", published_at=at,
            source="东方财富", url="https://example.com/news", category="A股", relevance_score=2,
        )],
    )


def save(path, facts):
    path.write_text(facts.model_dump_json(), encoding="utf-8")


def test_fast_loader_bounds_both_sources_without_losing_latest_low_relevance(tmp_path, monkeypatch):
    calls = []

    def source(name, count, **kwargs):
        calls.append((name, kwargs))
        return [news().items[0].model_copy(update={
            "title": f"{name}新闻 {index}", "relevance_score": 50,
            "published_at": NOW - timedelta(hours=1),
        }) for index in range(count)]

    def eastmoney(**kwargs):
        return source("东方财富", 200, **kwargs)

    def tonghuashun(**kwargs):
        rows = source("同花顺", 19, **kwargs)
        rows.append(news(title="刚刚发布的普通公司新闻").items[0].model_copy(update={"relevance_score": 0}))
        return rows

    monkeypatch.setattr(latest.finance_news, "_load_eastmoney", eastmoney)
    monkeypatch.setattr(latest.finance_news, "_load_tonghuashun", tonghuashun)
    facts, refreshing, stale, error = latest.LatestFinanceNewsStore(
        path=tmp_path / "latest.json", clock=lambda: NOW,
    ).read()
    assert len(facts.items) == 220
    assert any(item.title == "刚刚发布的普通公司新闻" for item in facts.items)
    assert not refreshing and not stale and error is None
    assert sorted(name for name, _ in calls) == ["东方财富", "同花顺"]
    assert all(kwargs == {"earliest": NOW - timedelta(hours=24), "max_pages": 1} for _, kwargs in calls)


def test_persists_and_reuses_fresh_snapshot_without_network_or_timestamp_change(tmp_path):
    path = tmp_path / "latest.json"
    calls = []
    store = latest.LatestFinanceNewsStore(path=path, loader=lambda: calls.append(1) or news(), clock=lambda: NOW)
    assert store.read() == (news(), False, False, None)
    assert path.exists()
    restarted = latest.LatestFinanceNewsStore(
        path=path, loader=lambda: pytest.fail("fresh persisted snapshot should be reused"),
        clock=lambda: NOW + timedelta(minutes=4),
    )
    assert restarted.read() == (news(), False, False, None)
    assert calls == [1]
    returned = store.read()[0]
    returned.items[0].title = "consumer mutation"
    assert store.read()[0].items[0].title == news().items[0].title


def test_expired_snapshot_returns_immediately_and_only_one_background_fetch_runs(tmp_path):
    path = tmp_path / "latest.json"
    old = news(NOW - timedelta(minutes=6))
    save(path, old)
    entered, release = Event(), Event()
    calls = []

    def loader():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return news()

    store = latest.LatestFinanceNewsStore(path=path, loader=loader, clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(store.read)
        try:
            assert entered.wait(3)
            assert first.result(timeout=1) == (old, True, True, None)
            joined = [pool.submit(store.read) for _ in range(10)]
            assert all(result.result(timeout=1) == (old, True, True, None) for result in joined)
            pending = store._pending
            assert pending is not None
        finally:
            release.set()
        assert pending.result(timeout=3) == news()
    assert calls == [1]
    assert store.read() == (news(), False, False, None)
    assert FinanceNewsFacts.model_validate_json(path.read_bytes()) == news()


def test_concurrent_cold_requests_wait_for_same_batch(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def loader():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return news()

    store = latest.LatestFinanceNewsStore(path=tmp_path / "latest.json", loader=loader, clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        requests = [pool.submit(store.read) for _ in range(4)]
        try:
            assert entered.wait(3)
            assert not any(request.done() for request in requests)
        finally:
            release.set()
        assert all(request.result(timeout=3) == (news(), False, False, None) for request in requests)
    assert calls == [1]


def test_refresh_failure_preserves_old_snapshot_and_backs_off_then_recovers(tmp_path):
    path = tmp_path / "latest.json"
    old = news(NOW - timedelta(minutes=6))
    save(path, old)
    current = [NOW]
    entered, release = Event(), Event()
    recovery_entered, recovery_release = Event(), Event()
    calls = []

    def loader():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        if len(calls) == 1:
            raise RuntimeError("secret deployment path /app/private/provider")
        recovery_entered.set()
        assert recovery_release.wait(5)
        return news(current[0])

    store = latest.LatestFinanceNewsStore(path=path, loader=loader, clock=lambda: current[0])
    try:
        assert store.read() == (old, True, True, None)
        assert entered.wait(3)
        pending = store._pending
    finally:
        release.set()
    with pytest.raises(latest.LatestFinanceNewsUnavailable, match=latest.REFRESH_ERROR):
        pending.result(timeout=3)
    assert store.read() == (old, False, True, latest.REFRESH_ERROR)
    assert FinanceNewsFacts.model_validate_json(path.read_bytes()) == old
    assert calls == [1]
    current[0] += timedelta(seconds=31)
    try:
        assert store.read() == (old, True, True, None)
        assert recovery_entered.wait(3)
        pending = store._pending
        assert store.read() == (old, True, True, None)
    finally:
        recovery_release.set()
    pending.result(timeout=3)
    assert store.read() == (news(current[0]), False, False, None)
    assert calls == [1, 1]


def test_cold_failure_has_generic_error_and_backoff(tmp_path):
    calls = []
    current = [NOW]

    def loader():
        calls.append(1)
        raise RuntimeError("private provider URL and secret path")

    store = latest.LatestFinanceNewsStore(path=tmp_path / "latest.json", loader=loader, clock=lambda: current[0])
    for _ in range(2):
        with pytest.raises(latest.LatestFinanceNewsUnavailable) as raised:
            store.read()
        assert str(raised.value) == latest.REFRESH_ERROR
    assert calls == [1]
    current[0] += timedelta(seconds=31)
    with pytest.raises(latest.LatestFinanceNewsUnavailable):
        store.read()
    assert calls == [1, 1]


@pytest.mark.parametrize("kind", ["corrupt", "oversized", "future", "expired", "naive", "query", "window", "no_sources"])
def test_invalid_persisted_snapshot_is_ignored_and_replaced(tmp_path, kind):
    path = tmp_path / "latest.json"
    invalid = news()
    if kind == "corrupt":
        path.write_text("not json", encoding="utf-8")
    elif kind == "oversized":
        path.write_bytes(b" " * (latest.MAX_SNAPSHOT_BYTES + 1))
    else:
        updates = {
            "future": {"fetched_at": NOW + timedelta(seconds=1)},
            "expired": {"fetched_at": NOW - timedelta(days=8)},
            "naive": {"fetched_at": NOW.replace(tzinfo=None)},
            "query": {"query": "company-specific query"},
            "window": {"window_hours": 48},
            "no_sources": {"sources": []},
        }
        save(path, invalid.model_copy(update=updates[kind]))
    calls = []
    store = latest.LatestFinanceNewsStore(path=path, loader=lambda: calls.append(1) or news(), clock=lambda: NOW)
    assert store.read() == (news(), False, False, None)
    assert calls == [1]


def test_successful_empty_recent_window_replaces_old_news_and_persists(tmp_path):
    path = tmp_path / "latest.json"
    old = news(NOW - timedelta(minutes=6))
    save(path, old)
    empty = news().model_copy(update={"items": []})
    entered, release = Event(), Event()

    def loader():
        entered.set()
        assert release.wait(5)
        return empty

    store = latest.LatestFinanceNewsStore(path=path, loader=loader, clock=lambda: NOW)
    try:
        assert store.read() == (old, True, True, None)
        assert entered.wait(3)
        pending = store._pending
    finally:
        release.set()
    assert pending.result(timeout=3) == empty
    assert store.read() == (empty, False, False, None)
    restarted = latest.LatestFinanceNewsStore(
        path=path, loader=lambda: pytest.fail("valid empty snapshot must be reused"), clock=lambda: NOW,
    )
    assert restarted.read() == (empty, False, False, None)


def test_disk_failure_keeps_memory_and_does_not_damage_prior_file(tmp_path, monkeypatch, caplog):
    path = tmp_path / "latest.json"
    path.write_text("previous content", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("private filesystem path")

    monkeypatch.setattr(latest.os, "replace", fail_replace)
    store = latest.LatestFinanceNewsStore(path=path, loader=lambda: news(), clock=lambda: NOW)
    assert store.read() == (news(), False, False, None)
    assert store.read() == (news(), False, False, None)
    assert path.read_text(encoding="utf-8") == "previous content"
    assert list(tmp_path.iterdir()) == [path]
    assert "private filesystem path" not in caplog.text


def test_get_latest_news_uses_configured_database_directory_lazily(tmp_path, monkeypatch):
    monkeypatch.setattr(latest, "_store", None)
    monkeypatch.setattr(latest, "get_database_path", lambda: tmp_path / "research.sqlite")
    monkeypatch.setattr(latest, "_now", lambda: NOW)
    monkeypatch.setattr(latest, "_load_latest", lambda now: news(now))
    assert not list(tmp_path.iterdir())
    facts, refreshing, stale, error = latest.get_latest_news()
    assert facts.fetched_at.tzinfo is not None
    assert not refreshing and not stale and error is None
    assert [path.name for path in tmp_path.iterdir()] == ["finance_news_latest.json"]
