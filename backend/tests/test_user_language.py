"""Field names stay internal across prose, streaming, and evidence tables."""
from types import SimpleNamespace

import pytest

from app.agents.user_language import PublicTextStream, field_names, field_mentions, validate_public_text
from app.agents.react_runtime.answer_delivery import AnswerDelivery
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.rendering import render_answer


@pytest.mark.parametrize("field", ["break_count", "next_open_pct", "second_break_count", "custom_metric_pct"])
def test_every_chunk_boundary_holds_internal_field_before_it_can_be_shown(field):
    names = field_names({"rows": [{field: 0}]})
    for boundary in range(1, len(field)):
        stream = PublicTextStream(names)
        first = stream.feed("样本：" + field[:boundary])
        second = stream.feed(field[boundary:] + "=0。")
        assert first == "样本：" and second == ""
        with pytest.raises(ValueError):
            stream.finish()


def test_readable_numbers_missing_values_links_and_financial_abbreviations_are_preserved():
    text = "盘中未炸板；次日开盘涨幅0.0%；换手率缺失。ETF、PE与000001.SZ。[来源](https://example.com/next_open_pct?q=break_count)。"
    stream = PublicTextStream()
    output = "".join(stream.feed(char) for char in text) + stream.finish()
    assert output == text and not field_mentions(text)
    assert field_mentions("字段next_open_pct缺失") == {"next_open_pct"}
    assert not field_mentions("other_next_open_pct_suffix")


def test_evidence_field_discovery_does_not_treat_prose_values_as_field_names():
    names = field_names({"custom_metric_pct": None, "description": "example_company"})
    with pytest.raises(ValueError):
        validate_public_text("custom_metric_pct缺失", names)
    validate_public_text("example_company", names)


def test_answer_delivery_hides_field_draft_and_accepts_repaired_chinese_revision():
    events = []
    delivery = AnswerDelivery(lambda name, payload: events.append((name, payload)))
    for chunk in ["已核对。break_", "count=0，next_", "open_pct缺失。"]:
        delivery.feed(chunk)
    assert "".join(p["delta"] for n, p in events if n == "answer_delta") == "已核对。"
    with pytest.raises(ValueError):
        delivery.render("已核对。break_count=0，next_open_pct缺失。")
    delivery.reset()
    answer = "盘中未炸板，次日开盘涨幅缺失。"
    delivery.feed(answer)
    delivery.render(answer)
    assert delivery.text == answer


def test_table_keeps_exact_evidence_field_and_value_but_requires_readable_title():
    record = {"result_state": "ok", "rows": [{"break_count": 0, "next_open_pct": None}]}
    store = SimpleNamespace(get=lambda _: record, scope_of=lambda _: "current_run")
    # Use the canonical scope value, independent of the test's prose labels.
    from app.agents.react_runtime.evidence import CURRENT_SCOPE
    store.scope_of = lambda _: CURRENT_SCOPE
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": "e1", "columns": [{"field": "break_count", "label": "break_count"}]})
    with pytest.raises(ValueError, match="labels"):
        render_answer(final, store)
    final.table.columns[0].label = "炸板次数"
    assert "| 0 |" in render_answer(final, store)
    final.table.columns[0].field = "next_open_pct"
    final.table.columns[0].label = "次日开盘涨幅"
    assert "| 缺失 |" in render_answer(final, store)
