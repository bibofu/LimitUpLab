import pytest

from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer


def sample(rows, **kwargs):
    store = EvidenceStore()
    key = store.add(tool="first_board_ratings", state="ok", arguments=kwargs,
                    payload={"top_candidates": rows, "universe_count": 58})
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": key, "columns": [{"field": "symbol", "label": "代码"},
                                       {"field": "name", "label": "名称"}]})
    return store, key, final


def test_long_list_is_rendered_exactly_without_preview_truncation():
    rows = [{"symbol": str(600000 + i), "name": f"样例{i}"} for i in range(83)]
    store, key, final = sample(rows)
    assert len(store.view(key)["rows"]) == 8
    answer = render_answer(final, store)
    assert answer.splitlines()[2:] == [f'| {r["symbol"]} | {r["name"]} |' for r in rows]
    assert len(answer.splitlines()) == 85


@pytest.mark.parametrize("symbols", [[], ["600001"]])
def test_count_scope_never_conflates_universe_and_returned_candidates(symbols):
    store, key, _ = sample([], symbols=symbols)
    meta = store.view(key)["metadata"]
    assert meta["universe_count"] == 58 and meta["returned_candidate_count"] == 0
    assert meta["count_scope"]["symbols_filtered"] == bool(symbols)
    assert "不是入池候选数" in meta["count_scope"]["universe_count"]


@pytest.mark.parametrize("change", ["history", "error", "missing", "nested", "truncated", "duplicate", "placeholder"])
def test_invalid_table_is_rejected_not_silently_repaired(change):
    store, key, final = sample([{"symbol": "600001", "name": "甲"}])
    record = store.get(key)
    if change == "history": record["historical_reference"] = True
    if change == "error": record["result_state"] = "error"
    if change == "missing": final.table.columns[0].field = "unknown"
    if change == "nested": record["rows"][0]["name"] = {"name": "甲"}
    if change == "truncated": record["source_truncated"] = True
    if change == "duplicate": final.table.columns.append(final.table.columns[0])
    if change == "placeholder": final.answer = "模型手写名单"
    with pytest.raises(ValueError): render_answer(final, store)


def test_table_escapes_markup_and_keeps_missing_values_explicit():
    store, _, final = sample([{"symbol": "600001", "name": "<script>甲|乙\n丙</script>"},
                              {"symbol": "600002", "name": None}])
    answer = render_answer(final, store)
    assert "<script>" not in answer and "&#124;" in answer
    assert answer.endswith("| 600002 | 缺失 |")


def test_existing_prose_finish_is_backward_compatible():
    assert render_answer(Finish(status="clarify", answer="请明确日期"), EvidenceStore()) == "请明确日期"
