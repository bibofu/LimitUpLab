"""Prepare and evaluate a double-human-labeled Live Eval Judge calibration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.chat_live_eval_judge_calibration import (
    CALIBRATION_VERSION,
    RUBRIC,
    build_human_label_sheet,
    evaluate_live_judge_calibration,
    select_calibration_trials,
)
from app.agents.chat_live_eval_runner import (
    JUDGE_PROMPT_VERSION,
    judge_dimensions_for_case,
    judge_live_answer,
    load_live_eval_dataset,
)
from app.config import configure_runtime_environment
from app.services.llm_provider import DEFAULT_OPENAI_BASE_URL, OpenAIChatCompletionsProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the independent Live Eval Judge.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("report", type=Path)
    prepare.add_argument("output_dir", type=Path)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("packet", type=Path)
    evaluate.add_argument("human_a", type=Path)
    evaluate.add_argument("human_b", type=Path)
    args = parser.parse_args()
    configure_runtime_environment()
    if args.command == "prepare":
        _prepare(args.report, args.output_dir)
    else:
        result = evaluate_live_judge_calibration(args.packet, args.human_a, args.human_b)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "failed":
            raise SystemExit(1)


def _prepare(report_path: Path, output_dir: Path) -> None:
    provider = _judge_provider()
    report = _read_json(report_path)
    cases = {case.case_id: case for case in load_live_eval_dataset().cases}
    candidates = []
    for result in report["results"]:
        case = cases[result["case_id"]]
        candidates.append({**result, "judge_dimensions": judge_dimensions_for_case(case)})
    selected = select_calibration_trials(candidates)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = output_dir / "judge_packet.json"
    existing = _read_json(packet_path) if packet_path.exists() else {"items": []}
    completed = {item["item_id"]: item for item in existing.get("items", [])}
    items = []
    for index, result in enumerate(selected, start=1):
        item_id = f"LJC-{index:03d}"
        if item_id in completed:
            existing_item = completed[item_id]
            if (
                existing_item.get("case_id") != result["case_id"]
                or existing_item.get("trial_index") != result["trial"]
            ):
                raise ValueError(f"resume item mismatch: {item_id}")
            items.append(existing_item)
            continue
        case = cases[result["case_id"]]
        print(f"[{index:02d}/50] judging {case.case_id} trial {result['trial']}", flush=True)
        judge_result = judge_live_answer(case, result, provider)
        items.append(
            {
                "item_id": item_id,
                "case_id": case.case_id,
                "trial_index": result["trial"],
                "category": case.category,
                "tags": case.tags,
                "question": case.turns[-1].user,
                "conversation_turns": [turn.model_dump(mode="json") for turn in case.turns],
                "expected_behavior": case.expected.response_behavior,
                "tool_facts": [
                    {"tool": trace["name"], "result": trace.get("result"), "output": trace.get("output")}
                    for trace in result["tool_trace"]
                ],
                "answer": result["answer"],
                "dimensions": result["judge_dimensions"],
                "deterministic_passed": result["passed"],
                "deterministic_failure_reasons": result["failure_reasons"],
                "judge_result": judge_result,
            }
        )
        _write_packet(packet_path, report, provider.model, items, "judge_generation_in_progress")
    packet = _write_packet(packet_path, report, provider.model, items, "awaiting_human_labels")
    human_a = output_dir / "human_a_labels.json"
    human_b = output_dir / "human_b_labels.json"
    if not human_a.exists():
        human_a.write_text(
            json.dumps(build_human_label_sheet(packet, "human_a"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not human_b.exists():
        human_b.write_text(
            json.dumps(build_human_label_sheet(packet, "human_b"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps({"packet": str(packet_path), "human_a": str(human_a), "human_b": str(human_b)}, indent=2))


def _write_packet(
    path: Path,
    report: dict,
    judge_model: str,
    items: list[dict],
    status: str,
) -> dict:
    packet = {
        "version": CALIBRATION_VERSION,
        "status": status,
        "source_run_id": report["run_id"],
        "judge_model": judge_model,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "rubric": RUBRIC,
        "items": items,
    }
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    return packet


def _judge_provider() -> OpenAIChatCompletionsProvider:
    model = os.getenv("LIMITUPLAB_EVAL_JUDGE_MODEL", "").strip()
    key = (
        os.getenv("LIMITUPLAB_EVAL_JUDGE_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not model or not key:
        raise SystemExit("Judge requires LIMITUPLAB_EVAL_JUDGE_MODEL and an available API key")
    return OpenAIChatCompletionsProvider(
        api_key=key,
        model=model,
        base_url=(
            os.getenv("LIMITUPLAB_EVAL_JUDGE_BASE_URL", "").strip()
            or os.getenv("LIMITUPLAB_LLM_BASE_URL", "").strip()
            or DEFAULT_OPENAI_BASE_URL
        ),
        thinking_enabled=False,
        native_function_calling_enabled=False,
    )


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


if __name__ == "__main__":
    main()
