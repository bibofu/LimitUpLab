"""Cross-file consistency checks for the offline Chat Eval V2 Golden dataset."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from app.agents.capability_contract import available_capability_names
from app.agents.chat_eval_dataset import RESULT_STATES, load_dev_dataset
from app.agents.tools import TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agent_chat_eval_tool_fixture_v2.json"
VALID_PRIMARY_TYPES = {"capability_base", "multi_turn", "composite", "failure", "safety", "out_of_scope"}
FAILURE_HINTS = ("不可用", "失败", "异常", "报错", "没有返回", "只返回部分", "数据缺失", "样本为空")
PLACEHOLDER_HISTORY = ("已返回结果", "已展示评分", "已列出股票", "已返回新闻", "已返回复盘")


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _relations(value: Any, prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if {"entity", "date", "metric", "value"} <= set(value):
            yield prefix, value
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            yield from _relations(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _relations(child, f"{prefix}[{index}]")


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for key, index in re.findall(r"(?:^|\.)([^.\[]+)|\[(\d+)\]", path):
        current = current[int(index)] if index else current[key]
    return current


def test_dataset_ids_tags_tools_and_schema_are_consistent() -> None:
    dataset = load_dev_dataset()
    fixture = _fixture()
    registry = {schema.name: schema for schema in TOOL_SCHEMAS}
    capabilities = set(available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES))
    ids = [case.case_id for case in dataset.cases]
    assert len(ids) == len(set(ids))

    for case in dataset.cases:
        assert case.primary_type in VALID_PRIMARY_TYPES
        valid_tags = VALID_PRIMARY_TYPES | capabilities
        assert all(tag in valid_tags or re.fullmatch(r"variant_[12]", tag) for tag in case.tags)
        required = set(case.expected.required_tools)
        optional = set(case.expected.optional_tools)
        forbidden = set(case.expected.forbidden_tools)
        assert not (required & optional or required & forbidden or optional & forbidden)
        assert required | optional | forbidden <= registry.keys()
        assert required <= fixture["tools"].keys()

        for tool, arguments in case.expected.tool_parameters.items():
            schema = registry[tool].args_schema
            properties = schema.get("properties", {})
            assert set(arguments) <= properties.keys(), (case.case_id, tool, arguments)
            assert set(schema.get("required", [])) <= arguments.keys(), (case.case_id, tool)
            for name, value in arguments.items():
                allowed = properties[name].get("enum")
                assert allowed is None or value in allowed, (case.case_id, tool, name, value)


def test_fixture_entity_map_and_evidence_paths_are_consistent() -> None:
    dataset = load_dev_dataset()
    fixture = _fixture()
    entities = fixture["entities"]
    name_to_symbol = {item["name"]: symbol for symbol, item in entities.items()}

    for tool, definition in fixture["tools"].items():
        for _, relation in _relations(definition["payload"]):
            symbol = relation.get("symbol")
            if symbol:
                assert symbol in entities, (tool, symbol)
                assert relation["entity"] == entities[symbol]["name"], (tool, relation)

    for case in dataset.cases:
        text = " ".join(turn.content for turn in case.conversation)
        mentioned_symbols = {symbol for symbol in entities if symbol in text}
        mentioned_names = {name for name in name_to_symbol if name in text}
        if mentioned_symbols and mentioned_names:
            assert all(
                name_to_symbol[name] in mentioned_symbols
                for name in mentioned_names
                if any(symbol in text for symbol in mentioned_symbols)
            ), case.case_id

        for claim in case.expected.evidence_claims:
            tool, _, relative_path = claim.source_path.partition(".")
            payload = fixture["tools"][tool]["payload"]
            assert _resolve_path(payload, relative_path) == claim.value, case.case_id
            record_path = relative_path.rsplit(".", 1)[0]
            record = _resolve_path(payload, record_path)
            assert record["entity"] == claim.entity
            assert record["date"] == claim.date
            assert record["metric"] == claim.metric
            if record.get("symbol"):
                assert entities[record["symbol"]]["name"] == claim.entity

        expected_date = case.expected.query.get("trade_date")
        if expected_date and case.expected.evidence_claims:
            assert any(claim.date == expected_date for claim in case.expected.evidence_claims), case.case_id


def test_question_semantics_match_query_goldens() -> None:
    dataset = load_dev_dataset()
    for case in dataset.cases:
        user_text = " ".join(turn.content for turn in case.conversation if turn.role == "user")
        last_question = case.conversation[-1].content
        query = case.expected.query
        symbols = re.findall(r"(?<!\d)([0368]\d{5})(?!\d)", last_question)
        if symbols:
            assert query.get("symbol") == symbols[-1], case.case_id
        if "半导体" in user_text and any(term in user_text for term in ("板块", "题材", "成分股")):
            assert query.get("sector") == "半导体", case.case_id
        if "跌停" in last_question and "涨停" not in last_question and "涨跌停" not in last_question:
            assert query.get("event_type") == "limit_down", case.case_id
        if any(term in last_question for term in ("炸板", "未回封")):
            assert query.get("event_type") == "broken_board", case.case_id
        if "三连板" in last_question:
            assert query.get("board_height") == 3, case.case_id
        if "首板" in last_question:
            assert query.get("board_height") == 1, case.case_id
        if "多少" in last_question:
            assert query.get("result_mode") == "count", case.case_id
        if any(term in last_question for term in ("总结", "汇总", "统计", "复盘", "审计", "比较", "共性", "共同特征")):
            assert query.get("result_mode") == "summary", case.case_id


def test_failure_states_are_injected_by_fixture_not_user_wording() -> None:
    dataset = load_dev_dataset()
    fixture = _fixture()
    overrides = fixture["case_overrides"]
    failure_cases = [case for case in dataset.cases if case.primary_type == "failure"]
    assert set(overrides) == {case.case_id for case in failure_cases}

    for case in failure_cases:
        question = case.conversation[-1].content
        assert not any(hint in question for hint in FAILURE_HINTS), case.case_id
        assert case.expected.response_behavior == "empty_disclosure"
        for tool, expected_state in case.expected.result_states.items():
            assert expected_state in RESULT_STATES - {"ok"}
            assert overrides[case.case_id][tool]["result_state"] == expected_state


def test_multi_turn_references_have_real_fixture_context() -> None:
    dataset = load_dev_dataset()
    fixture = _fixture()
    fixture_text = json.dumps(fixture["tools"], ensure_ascii=False)
    known_values = set(fixture["entities"])
    known_values.update(item["name"] for item in fixture["entities"].values())
    known_values.update(relation["entity"] for _, tool in fixture["tools"].items() for _, relation in _relations(tool["payload"]))
    cases = [case for case in dataset.cases if case.primary_type == "multi_turn"]
    for case in cases:
        assistant_history = " ".join(
            turn.content for turn in case.conversation[:-1] if turn.role == "assistant"
        )
        assert assistant_history
        assert not any(marker in assistant_history for marker in PLACEHOLDER_HISTORY)
        assert case.expected.query.get("context_reference"), case.case_id
        assert any(value in assistant_history for value in known_values), case.case_id
        assert any(value in fixture_text for value in known_values if value in assistant_history)


def test_safety_and_out_of_scope_cases_do_not_require_business_tools() -> None:
    dataset = load_dev_dataset()
    for case in dataset.cases:
        if case.primary_type in {"safety", "out_of_scope"}:
            assert not case.expected.required_tools
            assert not case.expected.optional_tools
            assert not case.expected.tool_parameters
