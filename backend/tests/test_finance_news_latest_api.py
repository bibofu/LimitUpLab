"""Latest batches never trigger the full-window history collector."""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import FinanceNewsFacts, FinanceNewsItem
from app.routers import market


def snapshot():
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    return FinanceNewsFacts(fetched_at=now, window_hours=24, sources=["测试来源"], items=[
        FinanceNewsItem(title=f"A股快讯{number}", summary="市场公开消息", source="测试来源",
                        published_at=now - timedelta(minutes=number), url=f"https://example.com/{number}",
                        category="A股", relevance_score=4) for number in reversed(range(30))
    ])


def test_latest_and_history_routes_use_independent_delivery_paths(monkeypatch):
    facts, calls = snapshot(), []
    history_facts = facts.model_copy(update={"window_hours": 12})
    def latest():
        calls.append("latest")
        return facts, True, True, None
    def history(**kwargs):
        assert kwargs == {"limit": 2000, "hours": 12}
        calls.append("history")
        return history_facts
    monkeypatch.setattr(market, "get_latest_news", latest)
    monkeypatch.setattr(market, "collect_finance_news", history)
    app = FastAPI()
    app.include_router(market.router, prefix="/api/market")
    with TestClient(app) as client:
        response = client.get("/api/market/news/latest?page_size=10")
        assert response.status_code == 200 and calls == ["latest"]
        body = response.json()
        assert body["total"] == 10 and body["total_pages"] == 1 and body["page"] == 1
        assert body["items"][0]["title"] == "A股快讯0"
        assert body["window_hours"] == 24
        assert body["refreshing"] and body["stale"] and body["refresh_error"] is None
        assert datetime.fromisoformat(body["fetched_at"]) == facts.fetched_at
        response = client.get("/api/market/news?page=2&page_size=10")
        assert calls == ["latest", "history"]
        assert response.json()["window_hours"] == 12
        assert response.json()["total"] == 30 and response.json()["total_pages"] == 3
        assert response.json()["items"][0]["title"] == "A股快讯10"
        assert not response.json()["refreshing"]
        assert client.get("/api/market/news/latest?page_size=2000").status_code == 422


def test_latest_error_preserves_cached_facts_or_returns_safe_unavailable(monkeypatch):
    facts = snapshot()
    monkeypatch.setattr(market, "get_latest_news", lambda: (facts, False, True, "新闻更新失败，显示上次快讯。"))
    result = market.get_latest_finance_news(page_size=10)
    assert result.fetched_at == facts.fetched_at and result.stale and result.items
    assert result.refresh_error == "新闻更新失败，显示上次快讯。"
    def unavailable():
        raise RuntimeError("private source details")
    monkeypatch.setattr(market, "get_latest_news", unavailable)
    app = FastAPI()
    app.include_router(market.router, prefix="/api/market")
    with TestClient(app) as client:
        result = client.get("/api/market/news/latest")
    assert result.status_code == 502 and "private" not in result.text
