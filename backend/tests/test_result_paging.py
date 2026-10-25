"""Lossless, bounded paging of current-run immutable evidence."""

from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agents.context_builder import estimate_tokens
from app.agents.result_paging import ReadModelResultRequest, ReadResultRequest, ResultPageError, content_hash, model_observation, read_result_page
from app.agents.runner.durable_runtime import execution_context
from app.agents.tool_context import expand_tool_observation, format_tool_observation
from app.repositories.tool_result_repository import ToolResultRepository
from app.routers.tool_results import router
from app.security import current_owner_id
from app.services.agent_result_reader import read_owned_result
from test_durable_agent import world
from test_tool_context import stock_list


def large_result():
    value = stock_list()
    value["rows"] = [dict(row, symbol=f"{600000 + index:06}")
                     for index, row in enumerate(value["rows"] * 20)]
    value["row_count"] = len(value["rows"])
    return value


def test_small_results_stay_lossless_and_large_results_have_explicit_manifest():
    small = stock_list()
    assert expand_tool_observation(model_observation(small, "one")) == small
    large = large_result()
    before = deepcopy(large)
    view = model_observation(large, "saved-call")
    assert view["result_view"]["kind"] == "manifest"
    assert view["result_view"]["rows_returned"] == 0 and "rows" not in view
    assert view["result_view"]["total_rows"] == 1360
    assert view["result_view"]["content_hash"] == content_hash(large)
    assert estimate_tokens(view) <= 6000
    assert large == before


def test_all_pages_reconstruct_exact_order_and_null_vs_missing():
    value = large_result()
    value["rows"][0]["optional"] = None
    value["rows"][2]["optional"] = {"value": 1}
    rows, offset = [], 0
    while True:
        page = read_result_page(value, ReadResultRequest(result_id="saved", offset=offset,
            expected_hash=content_hash(value), limit=100))
        rows.extend(page["rows"])
        assert page["total_rows"] == len(value["rows"])
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        offset = page["next_offset"]
    assert rows == value["rows"]
    assert rows[0]["optional"] is None and "optional" not in rows[1]


def test_projection_and_model_budget_never_cut_a_row_or_reorder():
    value = large_result()
    page = read_result_page(value, ReadResultRequest(result_id="saved", offset=42, limit=100,
        fields=["symbol", "name"]), max_tokens=1600)
    assert 0 < page["row_count"] < 100
    assert page["rows"] == [{key: row[key] for key in ("symbol", "name")}
                             for row in value["rows"][42:42 + page["row_count"]]]
    assert page["next_offset"] == 42 + page["row_count"]
    assert estimate_tokens(model_observation(page, "page-call")) <= 1600
    assert page["trade_date"] == value["trade_date"]


def test_model_page_global_positions_identify_exact_row_without_mutating_receipts():
    source = large_result()
    # A source field with the same name, even if projected out, is never shadowed.
    source["rows"][0]["_source_row_number"] = "business-value"
    source["rows"][39]["name"] = None
    source["rows"][40].pop("name")
    before = deepcopy(source)
    page = read_result_page(source, ReadResultRequest(result_id="saved", offset=35, limit=10,
        fields=["symbol", "name"]), max_tokens=5000)
    raw_page = deepcopy(page)
    view = expand_tool_observation(model_observation(page, "page-call"))
    field = view["row_position"]["field"]
    assert field != "_source_row_number"
    assert [row[field] for row in view["rows"]] == list(range(36, 46))
    fortieth = next(row for row in view["rows"] if row[field] == 40)
    assert fortieth["symbol"] == source["rows"][39]["symbol"] and fortieth["name"] is None
    assert "name" not in view["rows"][5]
    assert [{key: value for key, value in row.items() if key != field} for row in view["rows"]] == page["rows"]
    assert source == before and page == raw_page
    assert page["content_hash"] == content_hash(source)


@pytest.mark.parametrize("offset", [0, 67, 68])
def test_one_row_and_empty_page_positions_preserve_boundaries(offset):
    source = stock_list()
    page = read_result_page(source, ReadResultRequest(result_id="saved", offset=offset, limit=1))
    view = expand_tool_observation(model_observation(page, "page-call"))
    assert [row[view["row_position"]["field"]] for row in view["rows"]] == (
        [offset + 1] if offset < len(source["rows"]) else [])
    assert page["next_offset"] == (offset + 1 if offset + 1 < len(source["rows"]) else None)


@pytest.mark.parametrize("kwargs", [
    {"expected_hash": "a" * 64}, {"offset": 2000}, {"fields": ["invented_score"]},
    {"section": "metadata", "fields": ["not_a_source"]},
])
def test_invalid_or_changed_views_fail_explicitly(kwargs):
    with pytest.raises(ResultPageError):
        read_result_page(large_result(), ReadResultRequest(result_id="saved", **kwargs))


def test_model_reader_requires_manifest_hash_but_first_http_read_may_omit_it():
    with pytest.raises(ValueError):
        ReadModelResultRequest(result_id="saved")
    assert ReadResultRequest(result_id="saved").expected_hash is None


def test_metadata_is_explicitly_selected_and_oversized_values_are_not_cut():
    value = stock_list()
    value["source_notes"] = "完整来源内容" * 2000
    request = ReadResultRequest(result_id="saved", section="metadata", fields=["source_notes"])
    assert read_result_page(value, request)["metadata"] == {"source_notes": value["source_notes"]}
    with pytest.raises(ResultPageError, match="select fewer"):
        read_result_page(value, request, max_tokens=500)
    view = model_observation(value, "saved")
    assert "source_notes" in view["result_view"]["metadata_fields_available"]
    assert "source_notes" not in view


def test_owned_reader_survives_new_connection_without_rerunning_source(world):
    _, control, _ = world.start()
    value = large_result()
    with execution_context(SimpleNamespace(control=control)):
        ToolResultRepository(control).execute({"id": "source", "name": "fixed", "args": {}}, lambda: value)
    request = ReadResultRequest(result_id="source", offset=100, limit=3, expected_hash=content_hash(value))
    first = read_owned_result(world.url, owner_id=world.owner, run_id=control.row["run_id"], request=request)
    second = read_owned_result(world.url, owner_id=world.owner, run_id=control.row["run_id"], request=request)
    assert first == second and first["rows"] == value["rows"][100:103]
    with pytest.raises(KeyError):
        read_owned_result(world.url, owner_id="other-owner", run_id=control.row["run_id"], request=request)
    with pytest.raises(KeyError):
        read_owned_result(world.url, owner_id=world.owner, run_id="other-run", request=request)


def test_http_page_is_readonly_and_owner_scoped(world):
    _, control, _ = world.start()
    value = stock_list()
    with execution_context(SimpleNamespace(control=control)):
        ToolResultRepository(control).execute({"id": "source", "name": "fixed", "args": {}}, lambda: value)
    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    app.dependency_overrides[current_owner_id] = lambda: world.owner
    client = TestClient(app)
    path = f"/api/agents/chat/runs/{control.row['run_id']}/results/source"
    response = client.get(path, params={"offset": 3, "limit": 2, "fields": "symbol"})
    assert response.status_code == 200
    assert response.json()["rows"] == [{"symbol": row["symbol"]} for row in value["rows"][3:5]]
    assert response.headers["Cache-Control"] == "private, no-store"
    app.dependency_overrides[current_owner_id] = lambda: "other-owner"
    assert client.get(path).status_code == 404
    with world.lifecycle.connection() as db:
        assert db.execute("SELECT count(*) FROM agent_tool_results").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM chat_messages WHERE role='assistant'").fetchone()[0] == 0


def test_query_scope_uses_validated_arguments_and_effective_tool_defaults():
    from app.agents.runner.tool_view import model_result
    from app.agents.tools import ToolResult

    tool = ToolResult(name="limit_up_events", summary="固定来源",
        input={"trade_date": "2026-10-23", "market": "main", "event_status": "sealed",
               "board_height": 1, "sort_by": "symbol", "sort_order": "asc", "private_note": "not-an-argument"},
        output={})
    explicit = {"trade_date": "2026-10-23", "board_height": 1, "sort_by": "symbol"}
    observed = model_result("limit_up_events", tool, {"events": []}, "empty", arguments=explicit,
        allowed_arguments=["trade_date", "market", "event_status", "board_height", "sort_by", "sort_order"])
    scope = observed["query_scope"]
    assert scope["explicit_arguments"] == explicit
    assert scope["arguments"]["sort_order"] == "asc" and scope["arguments"]["market"] == "main"
    assert "private_note" not in scope["arguments"]
