"""Run the blind answer judge against the human-labeled calibration set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.services.review_calibration import calibration_gate, summarize_calibration
from app.services.review_judge import (
    DIMENSIONS,
    JudgeUnavailable,
    get_review_judge_provider,
    run_judge,
)
from app.services.review_snapshot import RunSnapshot


DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "evals" / "answer_review_cases.json"


def snapshot_from_case(case: dict) -> RunSnapshot:
    """Build the same bounded snapshot shape used by online review."""

    context = case.get("context") or {}
    evidence = dict(case.get("evidence") or {})
    evidence.update({
        "task_status": context.get("task_status"),
        "intent": context.get("intent"),
        "tool_calls": context.get("tool_calls") or [],
        "warnings": context.get("warnings") or [],
    })
    return RunSnapshot(
        run_id=str(case.get("case_id") or "calibration"),
        session_id="calibration",
        owner_id="calibration",
        question=str(case.get("question") or ""),
        answer=str(case.get("answer") or ""),
        intent=context.get("intent"),
        task_status=context.get("task_status"),
        trace_url=None,
        evidence=evidence,
    )


def evaluate_cases(
    cases: list[dict],
    provider,
    *,
    max_input_chars: int = 60000,
    minimum_cases: int = 30,
    minimum_kappa: float = 0.6,
) -> dict:
    """Judge every case and return agreement, kappa, errors and the gate."""

    predictions: dict[str, dict[str, str]] = {}
    errors: dict[str, str] = {}
    for case in cases:
        case_id = str(case.get("case_id"))
        try:
            outcome = run_judge(
                provider,
                snapshot_from_case(case),
                max_input_chars=max_input_chars,
            )
            predictions[case_id] = {
                item.dimension: item.verdict for item in outcome.report.dimensions
            }
        except JudgeUnavailable as error:
            errors[case_id] = type(error).__name__
            predictions[case_id] = {dimension: "unknown" for dimension in DIMENSIONS}
    summary = summarize_calibration(cases, predictions)
    gate = calibration_gate(
        summary,
        minimum_cases=minimum_cases,
        minimum_kappa=minimum_kappa,
    )
    return {
        "case_count": len(cases),
        "errors": errors,
        "predictions": predictions,
        "summary": summary,
        "gate": gate,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=Path("output/answer-review-calibration.json"))
    parser.add_argument("--limit", type=int, default=0, help="Judge at most this many cases")
    parser.add_argument("--minimum-cases", type=int, default=30)
    parser.add_argument("--minimum-kappa", type=float, default=0.6)
    args = parser.parse_args(argv)
    configure_runtime_environment()
    provider = get_review_judge_provider()
    if provider is None:
        print(
            "Judge is disabled or has no API key. Set LIMITUPLAB_REVIEW_JUDGE_ENABLED=true "
            "and configure the judge key before calibration.",
            file=sys.stderr,
        )
        return 2
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = list(dataset.get("cases") or [])
    if args.limit > 0:
        cases = cases[: args.limit]
    report = evaluate_cases(
        cases,
        provider,
        minimum_cases=args.minimum_cases,
        minimum_kappa=args.minimum_kappa,
    )
    report["dataset"] = str(args.dataset)
    report["version"] = dataset.get("version")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "cases": report["case_count"],
        "errors": len(report["errors"]),
        "overall_kappa": report["summary"]["overall"]["kappa"],
        "gate": report["gate"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
