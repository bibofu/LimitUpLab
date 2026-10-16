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
    assert answer.endswith("| 600002 | 数据未成功获取 |")


def test_existing_prose_finish_is_backward_compatible():
    assert render_answer(Finish(status="clarify", answer="请明确日期"), EvidenceStore()) == "请明确日期"


@pytest.mark.parametrize("separator", ["\n", "\n\n", "\r\n", "\r\n\r\n"])
def test_server_separates_table_from_prose_with_real_blank_lines(separator):
    store, _, final = sample([{"symbol": "600001", "name": "样例"}])
    final.answer = separator.join(["榜单如下。", "{{evidence_table}}", "说明：仅反映关注度。"])
    assert render_answer(final, store) == (
        "榜单如下。\n\n| 代码 | 名称 |\n| --- | --- |\n| 600001 | 样例 |\n\n说明：仅反映关注度。"
    )


@pytest.mark.parametrize("answer", [
    r"榜单如下。\n\n{{evidence_table}}\n\n说明：仅反映关注度。",
    "榜单如下。{{evidence_table}}",
    "{{evidence_table}}说明：仅反映关注度。",
    "榜单如下。\n{{evidence_table}}" + r"\n\n说明：仅反映关注度。",
    "`{{evidence_table}}`", "> {{evidence_table}}", "- {{evidence_table}}",
    "    {{evidence_table}}", "\t{{evidence_table}}",
    "```markdown\n{{evidence_table}}\n```",
    "~~~\n{{evidence_table}}\n~~~",
    "````markdown\n```\n\n{{evidence_table}}",
    "```\n~~~\n\n{{evidence_table}}",
])
def test_invalid_table_structure_requires_a_rewrite_not_unescaping(answer):
    store, _, final = sample([{"symbol": "600001", "name": "样例"}])
    final.answer = answer
    with pytest.raises(ValueError, match="Table placeholder"):
        render_answer(final, store)
    assert final.answer == answer


def test_valid_surrounding_code_and_literal_backslashes_are_not_reinterpreted():
    store, _, final = sample([{"symbol": "600001", "name": "样例"}])
    before = '路径 `C:\\new\\reports`，示例字面量 `\\n`。\n\n```text\nprint("\\n")\n```'
    after = '~~~text\nC:\\new\\reports\n~~~\n\n说明：样例结束。'
    final.answer = before + "\n{{evidence_table}}\n" + after
    rendered = render_answer(final, store)
    assert rendered.startswith(before + "\n\n| 代码")
    assert rendered.endswith("| 样例 |\n\n" + after)
    prose = Finish(status="clarify", answer=before)
    assert render_answer(prose, store) == before


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_table_boundaries_preserve_prose_spaces_and_following_code_indent(separator):
    store, _, final = sample([{"symbol": "600001", "name": "样例"}])
    before = "表前保留两个空格。  "
    after = separator.join([r"    C:\new\reports", r"    \n"])
    final.answer = separator.join([before, "  ", "{{evidence_table}}  ", "\t", after])
    rendered = render_answer(final, store)
    assert rendered.startswith(before + "\n\n| 代码")
    assert rendered.endswith("| 样例 |\n\n" + after)


@pytest.mark.parametrize("value,label", [
    ("up", "上升"), ("down", "下降"), ("flat", "持平"), (None, "数据未成功获取"),
    ("sideways", "未识别（sideways）"), (0, "未识别（0）"),
    ("<new>|other", "未识别（&lt;new&gt;&#124;other）"),
])
def test_rank_trend_is_presented_in_chinese_without_changing_evidence(value, label):
    rows = [{"symbol": "600001", "name": "样例", "rank_trend": value, "note": "up"}]
    store, key, final = sample(rows)
    final.table.columns = [
        type(final.table.columns[0])(field="rank_trend", label="排名趋势"),
        type(final.table.columns[0])(field="note", label="备注"),
    ]
    assert render_answer(final, store).endswith(f"| {label} | up |")
    assert store.get(key)["rows"] == rows


@pytest.mark.parametrize("value", [{"trend": "up"}, ["up"], float("nan")])
def test_rank_trend_mapping_does_not_bypass_scalar_validation(value):
    store, _, final = sample([{"symbol": "600001", "name": "样例", "rank_trend": value}])
    final.table.columns[0].field = "rank_trend"
    with pytest.raises(ValueError, match="finite scalar"):
        render_answer(final, store)
