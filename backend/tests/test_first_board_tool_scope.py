"""A saved Top cohort is not the complete eligible first-board rating pool."""

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from app.agents.first_board import build_first_board_ratings
from app.agents.runner.tool_view import model_result
from app.agents.tool_payload import payload_of
from app.agents.tools import AgentToolRegistry
from app.models import FirstBoardFilterResult, FirstBoardRatingsResponse
from app.repositories import FirstBoardRepository
from app.services.sample_data import SAMPLE_EVENTS


DAY = date(2026, 10, 23)


@pytest.fixture
def scoped_registry(monkeypatch):
    template = build_first_board_ratings(SAMPLE_EVENTS).candidates[0]
    candidates = [template.model_copy(update={
        "facts": template.facts.model_copy(update={"symbol": f"600{index:03}", "trade_date": DAY}),
    }) for index in range(59)]
    exclusions = [FirstBoardFilterResult(
        symbol=f"900{index:03}", name="被筛除事件", included=False,
        excluded_reasons=["非首板"], data_missing=[],
    ) for index in range(33)]
    full = FirstBoardRatingsResponse(
        trade_date=DAY, candidates=candidates, filtered_out=exclusions, universe_count=92,
        generated_by="test", data_as_of=DAY,
    )
    snapshot = full.model_copy(update={
        "candidates": candidates[:10], "snapshot_source": "live", "snapshot_top_limit": 10,
        "snapshot_created_at": datetime(2026, 10, 23, 8, tzinfo=timezone.utc),
    })
    repo = Mock(spec=FirstBoardRepository)
    repo.database_url = None
    repo.get_live_prediction_snapshot.return_value = snapshot
    monkeypatch.setattr("app.agents.tools._recommendation_intelligence_by_symbol", lambda *a, **kw: {})
    monkeypatch.setattr("app.agents.tools.build_first_board_ratings", lambda **kwargs: full)
    return AgentToolRegistry(SAMPLE_EVENTS, repo), snapshot, full


def visible(registry, symbols=None):
    result = registry.first_board_ratings(DAY, symbols=symbols)
    payload = payload_of(result)
    return result, payload, model_result("first_board_ratings", result, payload, "ok")


def test_saved_top10_is_distinct_from_59_eligible_and_33_hard_exclusions(scoped_registry):
    registry, snapshot, _ = scoped_registry
    result, payload, shown = visible(registry)
    assert result.output == snapshot  # Preserve typed snapshot/API compatibility.
    assert payload["selection_scope"] == "persisted_snapshot"
    assert payload["snapshot_top_limit"] == 10
    assert payload["returned_candidate_count"] == payload["snapshot_candidate_count"] == 10
    assert payload["universe_count"] == 92
    assert payload["filtered_out_count"] == len(payload["filtered_out"]) == 33
    assert payload["recorded_eligible_candidate_count"] == 59
    assert payload["not_selected_count"] == 49
    assert payload["source_truncated"] is True
    assert payload["candidate_pool_complete"] is False
    assert shown["truncated"] is True and shown["row_count"] == 10
    assert shown["recorded_eligible_candidate_count"] == 59
    assert "不是硬筛排除" in shown["field_definitions"]["metadata"]["not_selected_count"]
    assert "已固化" in result.summary and "入池10只" not in result.summary


def test_legacy_source_completeness_stays_unknown_and_ten_rows_do_not_imply_top10(scoped_registry):
    registry, snapshot, _ = scoped_registry
    registry.first_board_repository.get_live_prediction_snapshot.return_value = snapshot.model_copy(update={
        "snapshot_top_limit": None, "universe_count": 10, "filtered_out": [],
    })
    _, payload, shown = visible(registry)
    assert payload["snapshot_top_limit"] is None
    assert payload["source_truncated"] is None
    assert payload["candidate_pool_complete"] is None
    assert shown["candidate_pool_complete"] is None
    assert "不证明源快照完整" in shown["scope_note"]


def test_symbol_selection_does_not_turn_nonreturned_saved_candidates_into_hard_exclusions(scoped_registry):
    registry, _, _ = scoped_registry
    _, payload, shown = visible(registry, ["600000"])
    assert shown["row_count"] == payload["returned_candidate_count"] == 1
    assert payload["snapshot_candidate_count"] == 10
    assert payload["not_selected_count"] == 49
    assert payload["filtered_out_count"] == 33
    assert payload["requested_symbols"] == ["600000"]
    assert payload["candidate_pool_complete"] is False


@pytest.mark.parametrize("symbols", [None, ["600000"]])
def test_fresh_calculation_proves_full_source_pool_independently_of_symbol_selection(scoped_registry, symbols):
    registry, _, full = scoped_registry
    registry.first_board_repository.get_live_prediction_snapshot.return_value = None
    result, payload, shown = visible(registry, symbols)
    assert isinstance(result.output, FirstBoardRatingsResponse)
    assert payload["selection_scope"] == "calculated_full_pool"
    assert payload["snapshot_top_limit"] is None
    assert payload["snapshot_candidate_count"] == payload["recorded_eligible_candidate_count"] == len(full.candidates) == 59
    assert payload["not_selected_count"] == 0
    assert payload["source_truncated"] is False
    assert payload["candidate_pool_complete"] is (symbols is None)
    assert shown["row_count"] == (1 if symbols else 59)


def test_recorded_top_limit_is_not_inferred_from_candidate_count(scoped_registry):
    registry, snapshot, _ = scoped_registry
    registry.first_board_repository.get_live_prediction_snapshot.return_value = snapshot.model_copy(
        update={"snapshot_top_limit": 20},
    )
    _, payload, _ = visible(registry)
    assert payload["returned_candidate_count"] == 10
    assert payload["snapshot_top_limit"] == 20
