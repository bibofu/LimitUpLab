"""An overview cannot publish after its shared admission slot is lost."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.shared_agent_rate_limit import DatabaseAgentRateLimiter
from test_review_overview import DAYS, Provider, app, fixtures
from test_runtime_coordination import lease_store


@pytest.mark.parametrize("event", ["delta", "completed"])
def test_expired_overview_slot_rejects_publication(lease_store, monkeypatch, event):
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "true")
    limiter = DatabaseAgentRateLimiter(lease_store.database_url)
    lease = limiter.acquire("overview-owner", "ip", daily_request_count=0)
    report, events, repo = fixtures()

    def generate(*, emit, **kwargs):
        with lease_store.connection() as db:
            db.execute("UPDATE agent_request_slots SET expires_at=0 WHERE token=?", (lease.token,))
        emit(event, {"text": "must not be published"})

    with (
        patch("app.routers.review_overview.get_llm_provider", return_value=Provider()),
        patch("app.routers.review_overview.get_limit_up_repository", return_value=SimpleNamespace(list_events=lambda: events)),
        patch("app.routers.review_overview.FirstBoardRepository", return_value=repo),
        patch("app.routers.review_overview.load_promotion_calendar", return_value=SimpleNamespace(trade_dates=DAYS, warnings=())),
        patch("app.routers.review_overview.build_review_agent_report", return_value=report),
        patch("app.routers.review_overview.generate_overview", side_effect=generate),
        patch("app.routers.agents._begin_agent_request", return_value=(lease, Mock(), Mock())),
        patch("app.routers.agents._finish_agent_request") as finish,
        TestClient(app()) as client,
    ):
        response = client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "event: completed" not in response.text
    assert "must not be published" not in response.text
    assert str(finish.call_args.kwargs["error"]) == "LeaseLost"
    assert limiter.snapshot()["active_global"] == 0
