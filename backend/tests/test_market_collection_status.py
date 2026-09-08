"""Public collection status must not expose the privileged pipeline report."""
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from app.main import app
from app.models import DailyPipelineRun
from app.routers.market import get_market_collection_status


def test_collection_stage_is_public_and_sanitized():
    run = DailyPipelineRun(
        run_id="internal-id", trade_date=date(2026, 9, 8), trigger="scheduled",
        status="partial", attempt_count=1, started_at=datetime.now(timezone.utc),
        report={"phase": "preview", "internal_path": "private", "pipeline": {"warnings": ["private"]}},
        error_message="private provider failure",
    )
    with patch("app.routers.market.SQLiteDailyPipelineRepository") as repository:
        repository.return_value.list_recent = Mock(return_value=[run])
        response = get_market_collection_status()
    assert response.model_dump(mode="json") == {"trade_date": "2026-09-08", "phase": "preview", "status": "partial"}
    route = next(item for item in app.routes if getattr(item, "path", None) == "/api/market/collection-status")
    assert route.dependant.dependencies == []


def test_no_collection_run_does_not_claim_verified_data():
    with patch("app.routers.market.SQLiteDailyPipelineRepository") as repository:
        repository.return_value.list_recent = Mock(return_value=[])
        response = get_market_collection_status()
    assert response.model_dump(mode="json") == {"trade_date": None, "phase": None, "status": None}
