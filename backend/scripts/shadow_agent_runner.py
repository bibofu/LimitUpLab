"""Run the legacy and Phase 2 runners over the same questions.

This is an offline shadow harness: the legacy runtime is not changed and no
production/session state is written. Reports are stored under output/shadow/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import configure_runtime_environment  # noqa: E402

configure_runtime_environment()

from app.agents.chat import answer_first_board_chat  # noqa: E402
from app.agents.runner import AgentRunner  # noqa: E402
from app.agents.tools import AgentToolRegistry  # noqa: E402
from app.models import AgentChatRequest  # noqa: E402
from app.repositories import FirstBoardRepository, get_limit_up_repository  # noqa: E402
from app.services.llm_provider import get_llm_provider  # noqa: E402


def default_questions(events) -> list[str]:
    latest = max((event.trade_date for event in events), default=None)
    example = next((event for event in events if event.trade_date == latest), None)
    questions = [
        "最新交易日涨停多少只？其中首板、连板各多少？",
        "最新交易日有哪些连板股？请列出前5只。",
        "最新交易日首板候选里评分最高的是哪只？评分和评级是多少？",
        "最新交易日的龙虎榜有哪些股票？",
    ]
    if latest is not None:
        questions.append(f"{latest.isoformat()} 涨停多少只？首板、连板各多少？")
    if example is not None:
        questions.append(f"{example.name}最近20个交易日的走势如何？")
    return questions


def load_questions(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = line
        if isinstance(value, dict):
            value = value.get("question") or value.get("message")
        if isinstance(value, str) and value.strip():
            questions.append(value.strip())
    return questions


def compare(legacy, new) -> dict:
    legacy_tools = list(legacy.tool_calls)
    new_tools = list(new.tool_calls)
    return {
        "legacy_tools": legacy_tools,
        "new_tools": new_tools,
        "same_tool_set": set(legacy_tools) == set(new_tools),
        "tool_overlap": sorted(set(legacy_tools) & set(new_tools)),
        "legacy_status": legacy.task_status,
        "new_status": new.status,
        "legacy_stop_reason": legacy.stop_reason,
        "new_stop_reason": new.stop_reason,
        "legacy_chars": len(legacy.answer),
        "new_chars": len(new.answer),
        "legacy_latency_ms": legacy.performance.total_duration_ms,
        "new_latency_ms": new.duration_ms,
    }


def run_one(question: str, index: int, events, repository, provider, profile: str | None):
    request = AgentChatRequest(session_id=f"shadow-{index}", message=question)
    case = {"question": question}
    try:
        legacy = answer_first_board_chat(
            request=request,
            events=events,
            repository=repository,
            llm_provider=provider,
        )
        case["legacy"] = {
            "status": legacy.task_status,
            "stop_reason": legacy.stop_reason,
            "tool_calls": list(legacy.tool_calls),
            "latency_ms": legacy.performance.total_duration_ms,
            "answer": legacy.answer,
        }
    except Exception as error:  # noqa: BLE001 - shadow harness records failures
        legacy = None
        case["legacy"] = {"error": type(error).__name__, "detail": str(error)[:800]}

    try:
        registry = AgentToolRegistry(
            events=events,
            first_board_repository=repository,
            profile=profile,
        )
        new = AgentRunner(request, registry, provider).run()
        case["new"] = {
            "status": new.status,
            "stop_reason": new.stop_reason,
            "tool_calls": new.tool_calls,
            "latency_ms": new.duration_ms,
            "model_rounds": new.model_rounds,
            "tool_count": new.tool_count,
            "answer": new.answer,
            "error": new.error,
        }
    except Exception as error:  # noqa: BLE001 - shadow harness records failures
        new = None
        case["new"] = {"error": type(error).__name__, "detail": str(error)[:800]}

    if legacy is not None and new is not None:
        case["comparison"] = compare(legacy, new)
    return case


def build_report(cases: list[dict], *, model: str, profile: str) -> dict:
    comparable = [case["comparison"] for case in cases if "comparison" in case]
    legacy_errors = sum(bool(case.get("legacy", {}).get("error")) for case in cases)
    new_errors = sum(bool(case.get("new", {}).get("error")) for case in cases)
    legacy_latencies = [item["legacy_latency_ms"] for item in comparable]
    new_latencies = [item["new_latency_ms"] for item in comparable]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "profile": profile,
        "case_count": len(cases),
        "summary": {
            "comparable_count": len(comparable),
            "same_tool_set_count": sum(item["same_tool_set"] for item in comparable),
            "legacy_errors": legacy_errors,
            "new_errors": new_errors,
            "avg_legacy_latency_ms": round(sum(legacy_latencies) / len(legacy_latencies)) if legacy_latencies else None,
            "avg_new_latency_ms": round(sum(new_latencies) / len(new_latencies)) if new_latencies else None,
        },
        "cases": cases,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Agent Shadow Report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Model: `{report['model']}`",
        f"- Profile: `{report['profile']}`",
        f"- Cases: {report['case_count']}",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(report["summary"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Cases",
        "",
        "| # | Question | Legacy tools | New tools | Legacy status | New status | Legacy ms | New ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, case in enumerate(report["cases"], 1):
        legacy = case.get("legacy", {})
        new = case.get("new", {})
        lines.append(
            "| {index} | {question} | {legacy_tools} | {new_tools} | {legacy_status} | {new_status} | {legacy_ms} | {new_ms} |".format(
                index=index,
                question=str(case.get("question", "")).replace("|", "\\|")[:80],
                legacy_tools=", ".join(legacy.get("tool_calls", [])) or legacy.get("error", "-"),
                new_tools=", ".join(new.get("tool_calls", [])) or new.get("error", "-"),
                legacy_status=legacy.get("status", legacy.get("error", "-")),
                new_status=new.get("status", new.get("error", "-")),
                legacy_ms=legacy.get("latency_ms", "-"),
                new_ms=new.get("latency_ms", "-"),
            )
        )
    lines.extend(["", "## Answers", ""])
    for index, case in enumerate(report["cases"], 1):
        lines.append(f"### {index}. {case['question']}")
        lines.append("")
        lines.append("**Legacy**")
        lines.append("")
        lines.append(case.get("legacy", {}).get("answer", case.get("legacy", {}).get("error", "-")))
        lines.append("")
        lines.append("**New**")
        lines.append("")
        lines.append(case.get("new", {}).get("answer", case.get("new", {}).get("error", "-")))
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, help="Optional JSONL file; one question or {\"question\": ...} per line.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of cases to run.")
    parser.add_argument("--output", type=Path, help="Report directory; defaults to output/shadow/<timestamp>.")
    parser.add_argument("--profile", choices=("v1_close_review", "extended"), help="Agent profile for both runners.")
    args = parser.parse_args(argv)

    if args.profile:
        os.environ["LIMITUPLAB_AGENT_PROFILE"] = args.profile
    profile = args.profile or os.getenv("LIMITUPLAB_AGENT_PROFILE", "v1_close_review")

    events = get_limit_up_repository().list_events()
    if not events:
        parser.error("No local limit-up events are available.")
    questions = load_questions(args.questions) or default_questions(events)
    questions = questions[: max(1, args.limit)]

    repository = FirstBoardRepository()
    provider = get_llm_provider()

    cases = []
    for index, question in enumerate(questions, 1):
        print(f"[{index}/{len(questions)}] {question}", file=sys.stderr)
        cases.append(run_one(question, index, events, repository, provider, profile))
        print("  completed", file=sys.stderr)

    report = build_report(cases, model=getattr(provider, "model", type(provider).__name__), profile=profile)
    output = args.output or ROOT / "output" / "shadow" / datetime.now().strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
