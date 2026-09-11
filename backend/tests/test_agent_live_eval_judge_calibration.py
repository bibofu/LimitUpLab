import json

from app.agents.chat_live_eval_judge_calibration import (
    CALIBRATION_ITEM_COUNT,
    LIVE_JUDGE_DIMENSIONS,
    build_human_label_sheet,
    evaluate_live_judge_calibration,
    select_calibration_trials,
)


def _trial(case_id: str, category: str, trial: int, dimensions: list[str]) -> dict:
    return {
        "case_id": case_id,
        "category": category,
        "trial": trial,
        "judge_dimensions": dimensions,
    }


def _packet() -> dict:
    items = []
    for index in range(CALIBRATION_ITEM_COUNT):
        dimensions = list(LIVE_JUDGE_DIMENSIONS)
        items.append(
            {
                "item_id": f"LJC-{index + 1:03d}",
                "case_id": f"CASE-{index:03d}",
                "trial_index": 1,
                "category": "recovery",
                "question": "question",
                "conversation_turns": [{"user": "question"}],
                "expected_behavior": "answer",
                "tool_facts": [],
                "answer": "answer",
                "dimensions": dimensions,
                "judge_result": {"scores": {dimension: index % 3 for dimension in dimensions}},
            }
        )
    return {
        "version": "agent-live-eval-judge-calibration-v1",
        "source_run_id": "run-1",
        "judge_model": "judge-model",
        "prompt_version": "judge-prompt",
        "items": items,
    }


def test_calibration_selection_covers_dynamic_dimensions():
    results = []
    for category, case_count, dimensions in (
        ("simple", 6, ["completeness", "clarity", "relevance", "task_resolution"]),
        ("multi_tool", 2, ["completeness", "clarity", "relevance", "task_resolution", "risk_explanation"]),
        ("recovery", 4, ["completeness", "clarity", "relevance", "task_resolution", "uncertainty_disclosure", "failure_transparency"]),
        ("boundary", 4, ["clarity", "relevance", "task_resolution", "boundary_compliance"]),
        ("stress", 2, ["completeness", "clarity", "relevance", "task_resolution", "risk_explanation"]),
    ):
        for case_index in range(case_count):
            for trial in range(1, 4):
                results.append(_trial(f"{category}-{case_index}", category, trial, dimensions))
    selected = select_calibration_trials(results)

    assert len(selected) == 50
    assert sum(item["category"] == "boundary" for item in selected) >= 10
    assert sum(item["category"] == "recovery" for item in selected) >= 10
    assert sum("risk_explanation" in item["judge_dimensions"] for item in selected) >= 10


def test_human_sheets_are_blind_and_incomplete(tmp_path):
    packet = _packet()
    sheet_a = build_human_label_sheet(packet, "human_a")
    sheet_b = build_human_label_sheet(packet, "human_b")
    packet_path = tmp_path / "packet.json"
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    a_path.write_text(json.dumps(sheet_a), encoding="utf-8")
    b_path.write_text(json.dumps(sheet_b), encoding="utf-8")

    assert "judge_result" not in sheet_a["items"][0]
    assert sheet_a["items"][0]["scores"]["clarity"] is None
    result = evaluate_live_judge_calibration(packet_path, a_path, b_path)
    assert result["status"] == "awaiting_human_labels"
    assert result["missing_score_count"] == CALIBRATION_ITEM_COUNT * len(LIVE_JUDGE_DIMENSIONS)


def test_completed_double_labels_can_calibrate(tmp_path):
    packet = _packet()
    sheet_a = build_human_label_sheet(packet, "human_a")
    sheet_b = build_human_label_sheet(packet, "human_b")
    for index, (item_a, item_b) in enumerate(zip(sheet_a["items"], sheet_b["items"])):
        for dimension in item_a["dimensions"]:
            item_a["scores"][dimension] = index % 3
            item_b["scores"][dimension] = index % 3
    packet_path = tmp_path / "packet.json"
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    a_path.write_text(json.dumps(sheet_a), encoding="utf-8")
    b_path.write_text(json.dumps(sheet_b), encoding="utf-8")

    result = evaluate_live_judge_calibration(packet_path, a_path, b_path)

    assert result["status"] == "calibrated"
    assert result["passed"] is True
    assert all(metric["cohen_kappa"] == 1 for metric in result["dimensions"].values())
