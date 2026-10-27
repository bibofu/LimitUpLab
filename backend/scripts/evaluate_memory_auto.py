"""Run a small live calibration set for safe_auto policy decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.services.llm_provider import get_llm_provider
from app.services.memory_auto_policy import evaluate_safe_auto_candidate
from app.services.memory_extraction import candidate_key, extract_memory_candidates


CASES = (
    {"id": "tool_display", "message": "以后展示龙虎榜只显示股票名称", "expected": True},
    {"id": "global_style", "message": "以后回答简洁一点", "expected": True},
    {"id": "tool_style_exception", "message": "以后分析财报时详细一点", "expected": True},
    {"id": "entity_style_exception", "message": "以后分析600519时详细一点", "expected": True},
    {"id": "research_topic", "message": "以后研究主题只看首板", "expected": True},
    {"id": "temporary_request", "message": "这次先用表格", "expected": False},
    {"id": "delete_request", "message": "以后不要再记住回答简洁", "expected": False},
    {"id": "market_question", "message": "今天首板多吗", "expected": False},
    {"id": "trading_request", "message": "以后直接告诉我买入哪只", "expected": False},
    {"id": "condition_request", "message": "以后市场大跌时详细一点", "expected": False},
)


def evaluate_cases(provider, *, cases=CASES):
    results = []
    for case in cases:
        try:
            candidates = extract_memory_candidates(
                provider, message=case["message"], existing=[],
            )
        except Exception as error:  # noqa: BLE001 - report extraction failures as data
            results.append({**case, "actual": False, "decision": "error",
                            "error": type(error).__name__, "candidates": []})
            continue
        decisions = []
        for candidate in candidates:
            key = candidate_key(candidate)
            decision = evaluate_safe_auto_candidate(
                candidate, key=key, message=case["message"],
                consolidation={
                    "decision": "add",
                    "target_memory_id": None,
                    "superseded_candidate_id": None,
                },
            )
            decisions.append({
                "key": key,
                "scope_type": candidate.scope_type,
                "candidate_action": candidate.candidate_action,
                "eligible": decision.eligible,
                "reasons": list(decision.reasons),
            })
        actual = any(item["eligible"] for item in decisions)
        results.append({
            **case,
            "actual": actual,
            "decision": "auto_activate" if actual else "hold",
            "candidates": decisions,
        })
    return results


def summarize(results):
    true_positive = sum(item["expected"] and item["actual"] for item in results)
    true_negative = sum(not item["expected"] and not item["actual"] for item in results)
    false_positive = sum(not item["expected"] and item["actual"] for item in results)
    false_negative = sum(item["expected"] and not item["actual"] for item in results)
    errors = sum(item.get("decision") == "error" for item in results)
    total = len(results)
    return {
        "total": total,
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "errors": errors,
        "accuracy": round((true_positive + true_negative) / total, 3) if total else 0.0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True,
                        help="Use the configured LLM extractor; no database writes.")
    args = parser.parse_args(argv)
    configure_runtime_environment()
    results = evaluate_cases(get_llm_provider())
    print(json.dumps({
        "summary": summarize(results),
        "cases": results,
    }, ensure_ascii=False, indent=2))
    return 1 if summarize(results)["false_positive"] or summarize(results)["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
