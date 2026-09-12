from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.react_runtime.catalog import CATALOG
from app.agents.react_runtime.context import prepare_history
from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage


def gateway(**methods):
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda name: True, **methods)
    return ToolGateway(registry, EvidenceStore())


def test_every_existing_tool_has_reviewed_contract():
    assert {s.name for s in TOOL_SCHEMAS} == set(CATALOG)
    assert set(gateway().schemas) == set(CATALOG)


@pytest.mark.parametrize("name", ["prediction_quality_audit", "rating_backtest", "rating_evaluation", "review_high_score_picks"])
def test_required_dates_enforced_before_execution(name):
    with pytest.raises(ValueError, match="Missing required"):
        gateway().validate({"name": name, "args": {}})


def test_asof_guard_does_not_replace_history_with_current():
    with pytest.raises(ValueError, match="historical scope"):
        gateway().validate({"name": "hot_stock_ranking", "args": {"requested_as_of": "1990-01-01"}})


def test_first_board_filter_is_executable_not_virtual_capability():
    def ratings(trade_date=None):
        assert trade_date == date(2026, 5, 15)
        return ToolResult(name="first_board_ratings", input={}, summary="fixture", output={
            "trade_date": "2026-05-15", "candidates": [{"facts": {"symbol": "000001", "industry": "银行"}, "score": 80}],
        })
    result, payload, state = gateway(first_board_ratings=ratings).execute(
        "first_board_filter", {"query": "银行", "trade_date": "2026-05-15"},
    )
    assert state == "ok" and payload["items"][0]["symbol"] == "000001"


def test_partial_source_never_becomes_complete_set_difference():
    store = EvidenceStore()
    a = store.add(tool="limit_up_events", payload={"items": [{"symbol": "000001"}], "matched_count": 20}, state="ok", arguments={})
    b = store.add(tool="limit_up_events", payload={"items": []}, state="empty", arguments={})
    with pytest.raises(ValueError, match="complete source sets"):
        store.compute(Compute(evidence_id=a, other_id=b, operation="difference"))
    derived = store.compute(Compute(evidence_id=a, operation="aggregate"))
    assert store.get(derived)["result_state"] == "partial"


def test_historical_reference_only_loaded_from_requested_session():
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": [{"symbol": "000001"}]}, state="ok", arguments={})
    metadata = {"tool_results": [{"name": "react_execution", "output": {"evidence": store.records}}]}
    old = ChatSessionMessage(message_id="m", session_id="other", role="assistant", content="上一组", metadata=metadata, created_at="2026-09-12T00:00:00Z")
    target = EvidenceStore()
    messages, refs = prepare_history(AgentChatRequest(session_id="mine", message="这组"), [old], target)
    assert not messages and not refs and not target.records
    old.session_id = "mine"
    messages, refs = prepare_history(AgentChatRequest(session_id="mine", message="这组"), [old], target)
    assert refs[0]["evidence_id"] == key and target.get(key)["historical_reference"]


def test_empty_symbols_rejected_instead_of_unbounded_query():
    target = gateway(resolve_stock_identity=lambda value: (value, value))
    with pytest.raises(ValueError, match="Empty symbol set"):
        target.execute("first_board_ratings", {"symbols": []})
