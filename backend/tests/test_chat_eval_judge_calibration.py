import json

import pytest

from app.agents.chat_eval_judge_calibration import evaluate_judge_calibration


def _scores(value: int) -> dict[str, int]:
    return {
        "relevance": value,
        "completeness": value,
        "explanation": value,
        "uncertainty": value,
        "concision": value,
    }


def _artifact(*, poor_agreement: bool = False) -> dict:
    items = []
    for index in range(50):
        human_a = index % 3
        human_b = (human_a + 1) % 3 if poor_agreement and index < 20 else human_a
        items.append(
            {
                "item_id": f"JC-{index + 1:03d}",
                "human_a": _scores(human_a),
                "human_b": _scores(human_b),
                "judge": _scores(human_a),
            }
        )
    return {
        "version": "chat-eval-judge-calibration-v1",
        "judge_model": "judge-pinned-v1",
        "prompt_version": "chat-eval-judge-v1",
        "items": items,
    }


def test_fifty_item_double_label_calibration_enables_judge_gate(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")

    result = evaluate_judge_calibration(path)

    assert result["passed"] is True
    assert result["item_count"] == 50
    assert all(
        metrics["cohen_kappa"] == 1
        and metrics["human_agreement"] == 1
        and metrics["judge_consensus_agreement"] == 1
        for metrics in result["dimensions"].values()
    )


def test_calibration_rejects_low_human_agreement(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(_artifact(poor_agreement=True)), encoding="utf-8"
    )
    assert evaluate_judge_calibration(path)["passed"] is False


def test_calibration_requires_exactly_fifty_items(tmp_path):
    payload = _artifact()
    payload["items"].pop()
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 50"):
        evaluate_judge_calibration(path)
