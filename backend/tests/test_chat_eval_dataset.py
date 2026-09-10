"""Dataset-contract tests for Chat Eval V2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.chat_eval_dataset import (
    CHAT_EVAL_DATASET_VERSION,
    CHAT_EVAL_FIXTURE_ID,
    DEV_PRIMARY_TYPE_COUNTS,
    HOLDOUT_PATH_ENV,
    ChatEvalCase,
    load_dev_dataset,
    load_chat_eval_dataset,
    load_dataset_selection,
    load_holdout_dataset,
)
from app.agents.capability_contract import available_capability_names
from app.agents.tools import V1_CLOSED_MARKET_TOOL_NAMES


def _case_payload(
    index: int,
    capability: str,
    *,
    dataset: str = "dev",
    primary_type: str = "capability_base",
) -> dict:
    prefix = "D" if dataset == "dev" else "H"
    return {
        "case_id": f"CEV2-{prefix}{index:03d}",
        "dataset": dataset,
        "profile": "v1_close_review",
        "severity": "normal",
        "primary_type": primary_type,
        "tags": [capability],
        "fixture_snapshot_id": CHAT_EVAL_FIXTURE_ID,
        "anchor_datetime": "2026-09-10T18:00:00+08:00",
        "conversation": [{"role": "user", "content": f"问题{prefix}{index}"}],
        "expected": {
            "query": {},
            "allowed_capability_sets": [[capability]],
            "required_tools": [],
            "forbidden_tools": [],
            "tool_parameters": {},
            "policy_repairs": {},
            "result_states": {},
            "evidence_claims": [],
            "response_behavior": "answer",
            "answer_assertions": {},
        },
    }


def _dataset_payload(split: str) -> dict:
    capabilities = list(available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES))
    per_capability = 3 if split == "dev" else 1
    distribution = (
        DEV_PRIMARY_TYPE_COUNTS
        if split == "dev"
        else {
            "capability_base": 23,
            "multi_turn": 6,
            "composite": 4,
            "failure": 3,
            "safety": 2,
            "out_of_scope": 2,
        }
    )
    cases = []
    index = 1
    for capability in capabilities:
        for _ in range(per_capability):
            cases.append(_case_payload(index, capability, dataset=split))
            index += 1
    for primary_type, count in distribution.items():
        if primary_type == "capability_base":
            continue
        for _ in range(count):
            case = _case_payload(
                index,
                capabilities[0],
                dataset=split,
                primary_type=primary_type,
            )
            if primary_type == "multi_turn":
                case["conversation"] = [
                    {"role": "user", "content": f"首问{index}"},
                    {"role": "assistant", "content": "上下文事实"},
                    {"role": "user", "content": f"追问{index}"},
                ]
            cases.append(case)
            index += 1
    return {
        "version": CHAT_EVAL_DATASET_VERSION,
        "dataset": split,
        "description": "test",
        "cases": cases,
    }


def _write_dataset(tmp_path: Path, split: str) -> Path:
    path = tmp_path / f"{split}.json"
    path.write_text(
        json.dumps(_dataset_payload(split), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_loader_accepts_exact_dev_distribution(tmp_path: Path) -> None:
    dataset = load_chat_eval_dataset(
        _write_dataset(tmp_path, "dev"), expected_split="dev"
    )
    assert len(dataset.cases) == 120
    assert dataset.version == CHAT_EVAL_DATASET_VERSION


def test_committed_dev_dataset_covers_every_v1_capability_three_times() -> None:
    dataset = load_dev_dataset()
    capability_cases = [
        case for case in dataset.cases if case.primary_type == "capability_base"
    ]
    observed = [case.expected.allowed_capability_sets[0][0] for case in capability_cases]
    capabilities = set(available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES))
    assert len(dataset.cases) == 120
    assert set(observed) == capabilities
    assert all(observed.count(capability) == 3 for capability in capabilities)
    assert all(case.expected.query for case in dataset.cases)
    assert sum(bool(case.expected.tool_parameters) for case in dataset.cases) >= 100
    assert sum(len(case.expected.evidence_claims) for case in dataset.cases) >= 100
    assert all(case.expected.forbidden_tools for case in dataset.cases)
    for case in dataset.cases:
        for claim in case.expected.evidence_claims:
            tool = claim.source_path.partition(".")[0]
            assert tool in case.expected.required_tools
            assert case.expected.result_states[tool] in {"ok", "partial"}


def test_loader_rejects_distribution_drift(tmp_path: Path) -> None:
    payload = _dataset_payload("dev")
    payload["cases"][69]["primary_type"] = "composite"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="distribution"):
        load_chat_eval_dataset(path, expected_split="dev")


def test_case_rejects_naive_anchor_and_overlapping_tools() -> None:
    payload = _case_payload(1, "limit_up_pool")
    payload["anchor_datetime"] = "2026-09-10T18:00:00"
    with pytest.raises(ValueError, match="timezone"):
        ChatEvalCase.model_validate(payload)

    payload = _case_payload(1, "limit_up_pool")
    payload["expected"]["required_tools"] = ["limit_up_events"]
    payload["expected"]["forbidden_tools"] = ["limit_up_events"]
    with pytest.raises(ValueError, match="both required and forbidden"):
        ChatEvalCase.model_validate(payload)


def test_holdout_requires_private_environment_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(HOLDOUT_PATH_ENV, raising=False)
    with pytest.raises(ValueError, match=HOLDOUT_PATH_ENV):
        load_holdout_dataset()


def test_all_selection_combines_dev_and_private_holdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev_path = _write_dataset(tmp_path, "dev")
    holdout_path = _write_dataset(tmp_path, "holdout")
    monkeypatch.setenv(HOLDOUT_PATH_ENV, str(holdout_path))
    monkeypatch.setattr(
        "app.agents.chat_eval_dataset.DEV_DATASET_PATH", dev_path
    )
    cases = load_dataset_selection("all")
    assert len(cases) == 160
    assert {case.dataset for case in cases} == {"dev", "holdout"}
